"""
PhoneBridge — rclone Mount Manager

Manages mounting and unmounting of phone WebDAV servers as Windows drive letters
using rclone subprocess calls.
"""
import logging
import subprocess
import threading
import time
import urllib.request
import ssl
import base64
from dataclasses import dataclass, field
from typing import Optional, Callable

from .utils import check_rclone, check_winfsp, get_available_drive_letters
from .discovery import DiscoveredPhone

logger = logging.getLogger("phonebridge.mounter")

# Markers in rclone stderr that indicate an authentication failure (401)
AUTH_ERROR_MARKERS = [
    "401",
    "unauthorized",
    "authentication failed",
    "access denied",
    "auth failed",
    "login failed",
    "invalid credentials",
]


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


class AuthError(MountError):
    """Raised specifically when mount fails due to invalid credentials (401)."""
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
        on_auth_failed: Optional[Callable[[str], None]] = None,
    ):
        self._rclone_path = rclone_path or check_rclone()
        self._vfs_cache_mode = vfs_cache_mode
        self._vfs_cache_max_age = vfs_cache_max_age
        self._vfs_read_chunk_size = vfs_read_chunk_size

        self._on_mount = on_mount
        self._on_unmount = on_unmount
        self._on_error = on_error
        self._on_auth_failed = on_auth_failed

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

    @staticmethod
    def is_auth_error(error_text: str) -> bool:
        """Check if an error message indicates an authentication failure."""
        lower = error_text.lower()
        return any(marker in lower for marker in AUTH_ERROR_MARKERS)

    def check_auth(self, webdav_url: str, user: str, password: str) -> bool:
        """
        Probe the WebDAV server to verify credentials before mounting.
        
        Returns True if auth succeeds, raises AuthError if 401,
        raises MountError for other connection failures.
        """
        try:
            # Build an OPTIONS request (lightweight, doesn't transfer data)
            req = urllib.request.Request(webdav_url, method="OPTIONS")
            if user and password:
                credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
                req.add_header("Authorization", f"Basic {credentials}")

            # Accept self-signed certs
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                logger.debug(f"Auth check passed (HTTP {resp.status})")
                return True
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise AuthError(
                    f"Authentication failed (HTTP 401). "
                    f"The connection password may have been changed on the phone."
                )
            raise MountError(f"Server returned HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise MountError(f"Cannot reach phone: {e.reason}")
        except Exception as e:
            raise MountError(f"Connection check failed: {e}")

    def is_server_reachable(self, webdav_url: str) -> bool:
        """
        Quick check if the WebDAV server is actually running and reachable.
        Returns True if server responds, False otherwise.
        """
        try:
            req = urllib.request.Request(webdav_url, method="OPTIONS")
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
                return True
        except urllib.error.HTTPError:
            # Server responded (even if 401) — it's running
            return True
        except Exception:
            return False

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

        # Check if already mounted (by device_id)
        with self._lock:
            if phone.device_id in self._mounts:
                existing = self._mounts[phone.device_id]
                if existing.is_alive:
                    logger.warning(f"Phone {phone.display_name} already mounted on {existing.drive_letter}")
                    return existing
                else:
                    # Stale mount — clean up
                    self._cleanup_mount(phone.device_id)

            # Also check by IP/URL — prevents double mounts from mDNS name collisions
            for did, m in self._mounts.items():
                if m.webdav_url == phone.webdav_url and m.is_alive:
                    logger.warning(
                        f"Phone at {phone.webdav_url} already mounted as {m.drive_letter} "
                        f"(device_id={did}), skipping duplicate mount"
                    )
                    return m

        # Verify server is actually running before starting rclone
        if not self.is_server_reachable(phone.webdav_url):
            raise MountError(
                f"Server on {phone.display_name} is not reachable. "
                f"Make sure PhoneBridge is running on your phone."
            )

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
            "--skip-links",
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
        """Periodically check mount health — both process and auth."""
        while self._running:
            with self._lock:
                mounts = list(self._mounts.items())

            for device_id, mount in mounts:
                if not mount.is_alive:
                    # ── Process died ──────────────────────────────
                    logger.warning(f"Mount died: {mount.display_name} ({mount.drive_letter})")
                    stderr = ""
                    if mount.process and mount.process.stderr:
                        try:
                            stderr = mount.process.stderr.read().decode("utf-8", errors="replace")
                        except Exception:
                            pass
                    
                    mount.error = stderr or "Process exited unexpectedly"
                    is_auth = self.is_auth_error(mount.error)
                    
                    if is_auth:
                        logger.warning(
                            f"\U0001f511 Auth failure detected for {mount.display_name} — "
                            f"password was likely changed on the phone"
                        )
                    
                    with self._lock:
                        self._mounts.pop(device_id, None)

                    if is_auth and self._on_auth_failed:
                        self._on_auth_failed(device_id)
                    elif self._on_error:
                        self._on_error(device_id, mount.error)
                else:
                    # ── Process alive — probe credentials ────────
                    # rclone stays alive even when the server returns 401,
                    # but all I/O operations fail. Detect this proactively.
                    if mount.auth_user and mount.auth_password:
                        try:
                            self.check_auth(
                                mount.webdav_url,
                                mount.auth_user,
                                mount.auth_password,
                            )
                        except AuthError:
                            logger.warning(
                                f"\U0001f511 Credentials rejected for live mount "
                                f"{mount.display_name} ({mount.drive_letter}) — "
                                f"password was changed on phone, unmounting..."
                            )
                            # Kill the rclone process and clean up
                            self._kill_mount_process(mount)
                            with self._lock:
                                self._mounts.pop(device_id, None)
                            if self._on_auth_failed:
                                self._on_auth_failed(device_id)
                        except (MountError, Exception) as e:
                            # Network hiccup — don't treat as auth failure
                            logger.debug(f"Health probe failed (non-auth): {e}")

            time.sleep(5)
