"""
PhoneBridge — rclone Mount Manager

Manages mounting and unmounting of phone WebDAV servers as Windows drive letters
using rclone subprocess calls.
"""
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

from .utils import check_rclone, check_winfsp, get_available_drive_letters
from .discovery import DiscoveredPhone

logger = logging.getLogger("phonebridge.mounter")


@dataclass
class MountInfo:
    """Tracks state of a mounted phone."""
    device_id: str
    display_name: str
    drive_letter: str
    webdav_url: str
    auth_user: str = ""
    auth_password: str = ""
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    mounted_at: float = 0.0
    error: str = ""

    @property
    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


class MountError(Exception):
    """Raised when a mount operation fails."""
    pass


class MountManager:
    """
    Manages rclone mount/unmount operations for phone WebDAV servers.
    
    Usage:
        manager = MountManager(rclone_path="rclone")
        manager.mount(phone, "E:")
        ...
        manager.unmount("device_id")
    """

    def __init__(
        self,
        rclone_path: Optional[str] = None,
        vfs_cache_mode: str = "full",
        vfs_cache_max_age: str = "1h",
        vfs_read_chunk_size: str = "32M",
        on_mount: Optional[Callable[[MountInfo], None]] = None,
        on_unmount: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str, str], None]] = None,
    ):
        self._rclone_path = rclone_path or check_rclone()
        self._vfs_cache_mode = vfs_cache_mode
        self._vfs_cache_max_age = vfs_cache_max_age
        self._vfs_read_chunk_size = vfs_read_chunk_size

        self._on_mount = on_mount
        self._on_unmount = on_unmount
        self._on_error = on_error

        self._mounts: dict[str, MountInfo] = {}
        self._lock = threading.Lock()
        self._health_thread: Optional[threading.Thread] = None
        self._running = False

    def check_dependencies(self) -> list[str]:
        """
        Check if all required dependencies are installed.
        Returns a list of missing dependency names.
        """
        missing = []
        if not self._rclone_path:
            missing.append("rclone")
        if not check_winfsp():
            missing.append("WinFsp")
        return missing

    def start_health_monitor(self):
        """Start background thread that monitors mount health."""
        self._running = True
        self._health_thread = threading.Thread(
            target=self._health_check_loop,
            daemon=True,
            name="mount-health-monitor",
        )
        self._health_thread.start()
        logger.info("Mount health monitor started")

    def stop_health_monitor(self):
        """Stop the health monitoring thread."""
        self._running = False

    def _obscure_password(self, password: str) -> str:
        """
        Obscure a password using rclone's built-in obscure command.
        
        rclone requires passwords passed via --webdav-pass to be in its
        obscured format (a reversible encoding), not plaintext.
        """
        if not self._rclone_path or not password:
            return password

        try:
            result = subprocess.run(
                [self._rclone_path, "obscure", password],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            if result.returncode == 0:
                obscured = result.stdout.strip()
                logger.debug("Password obscured successfully")
                return obscured
            else:
                logger.warning(f"rclone obscure failed: {result.stderr}")
                return password
        except Exception as e:
            logger.warning(f"Failed to obscure password: {e}")
            return password

    def mount(
        self,
        phone: DiscoveredPhone,
        drive_letter: str,
        auth_user: str = "",
        auth_password: str = "",
        timeout: float = 10.0,
    ) -> MountInfo:
        """
        Mount a phone's WebDAV server as a Windows drive letter.
        
        Args:
            phone: Discovered phone to mount
            drive_letter: Target drive letter (e.g., "E:")
            auth_user: Username for Basic Auth (if required)
            auth_password: Password for Basic Auth (if required)
            timeout: Seconds to wait for mount to establish
            
        Returns:
            MountInfo with mount details
            
        Raises:
            MountError: If mounting fails
        """
        if not self._rclone_path:
            raise MountError(
                "rclone is not installed. Download from https://rclone.org/downloads/"
            )

        # Check if already mounted
        with self._lock:
            if phone.device_id in self._mounts:
                existing = self._mounts[phone.device_id]
                if existing.is_alive:
                    logger.warning(f"Phone {phone.display_name} already mounted on {existing.drive_letter}")
                    return existing
                else:
                    # Stale mount — clean up
                    self._cleanup_mount(phone.device_id)

        # Check if drive letter is already in use
        with self._lock:
            used_letters = {m.drive_letter for m in self._mounts.values() if m.is_alive}
            if drive_letter in used_letters:
                raise MountError(f"Drive letter {drive_letter} is already in use")

        logger.info(f"Mounting {phone.display_name} → {drive_letter} ({phone.webdav_url})")

        # Build rclone command
        cmd = [
            self._rclone_path,
            "mount",
            f":webdav:",
            drive_letter,
            f"--webdav-url={phone.webdav_url}",
            f"--vfs-cache-mode={self._vfs_cache_mode}",
            f"--vfs-cache-max-age={self._vfs_cache_max_age}",
            f"--vfs-read-chunk-size={self._vfs_read_chunk_size}",
            "--dir-cache-time=5s",
            "--poll-interval=10s",
            "--vfs-write-back=0s",
            f"--volname=PhoneBridge ({phone.display_name})",
            "--log-level=NOTICE",
            "--no-console",
            "--network-mode",
        ]

        # Add auth credentials if provided
        if auth_user and auth_password:
            obscured_pass = self._obscure_password(auth_password)
            cmd.append(f"--webdav-user={auth_user}")
            cmd.append(f"--webdav-pass={obscured_pass}")
            logger.info(f"  Auth: Basic (user={auth_user})")

        # Add HTTPS certificate trust for self-signed certs
        if phone.webdav_url.startswith("https://"):
            cmd.append("--no-check-certificate")
            logger.info("  TLS: Accepting self-signed certificate")

        try:
            # Start rclone process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )

            # Wait a moment and check if it started successfully
            time.sleep(2)
            if process.poll() is not None:
                stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
                raise MountError(f"rclone exited immediately: {stderr}")

            mount_info = MountInfo(
                device_id=phone.device_id,
                display_name=phone.display_name,
                drive_letter=drive_letter,
                webdav_url=phone.webdav_url,
                auth_user=auth_user,
                auth_password=auth_password,
                process=process,
                mounted_at=time.time(),
            )

            with self._lock:
                self._mounts[phone.device_id] = mount_info

            logger.info(f"✅ Mounted {phone.display_name} → {drive_letter}")

            if self._on_mount:
                self._on_mount(mount_info)

            return mount_info

        except MountError:
            raise
        except Exception as e:
            error_msg = f"Failed to mount {phone.display_name}: {e}"
            logger.error(error_msg)
            raise MountError(error_msg) from e

    def unmount(self, device_id: str):
        """Unmount a phone by device ID."""
        with self._lock:
            mount_info = self._mounts.get(device_id)

        if not mount_info:
            logger.warning(f"No mount found for device: {device_id}")
            return

        logger.info(f"Unmounting {mount_info.display_name} from {mount_info.drive_letter}...")
        self._kill_mount_process(mount_info)

        with self._lock:
            self._mounts.pop(device_id, None)

        if self._on_unmount:
            self._on_unmount(device_id)

        logger.info(f"✅ Unmounted {mount_info.display_name} from {mount_info.drive_letter}")

    def unmount_all(self):
        """Unmount all phones."""
        with self._lock:
            device_ids = list(self._mounts.keys())

        for device_id in device_ids:
            try:
                self.unmount(device_id)
            except Exception as e:
                logger.error(f"Error unmounting {device_id}: {e}")

    def get_mounts(self) -> dict[str, MountInfo]:
        """Get all active mounts."""
        with self._lock:
            return {k: v for k, v in self._mounts.items() if v.is_alive}

    def is_mounted(self, device_id: str) -> bool:
        """Check if a phone is currently mounted."""
        with self._lock:
            mount = self._mounts.get(device_id)
            return mount is not None and mount.is_alive

    def get_next_drive_letter(self) -> Optional[str]:
        """Get the next available drive letter."""
        available = get_available_drive_letters()
        with self._lock:
            used = {m.drive_letter for m in self._mounts.values() if m.is_alive}
        
        for letter in available:
            if letter not in used:
                return letter
        return None

    def _kill_mount_process(self, mount_info: MountInfo):
        """Kill the rclone process for a mount."""
        if mount_info.process and mount_info.process.poll() is None:
            try:
                mount_info.process.terminate()
                mount_info.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mount_info.process.kill()
                mount_info.process.wait(timeout=3)
            except Exception as e:
                logger.error(f"Error killing process: {e}")

    def _cleanup_mount(self, device_id: str):
        """Clean up a stale mount entry."""
        with self._lock:
            mount = self._mounts.pop(device_id, None)
        if mount:
            self._kill_mount_process(mount)

    def _health_check_loop(self):
        """Periodically check mount health."""
        while self._running:
            with self._lock:
                mounts = list(self._mounts.items())

            for device_id, mount in mounts:
                if not mount.is_alive:
                    logger.warning(f"Mount died: {mount.display_name} ({mount.drive_letter})")
                    stderr = ""
                    if mount.process and mount.process.stderr:
                        try:
                            stderr = mount.process.stderr.read().decode("utf-8", errors="replace")
                        except Exception:
                            pass
                    
                    mount.error = stderr or "Process exited unexpectedly"
                    
                    with self._lock:
                        self._mounts.pop(device_id, None)

                    if self._on_error:
                        self._on_error(device_id, mount.error)

            time.sleep(5)
