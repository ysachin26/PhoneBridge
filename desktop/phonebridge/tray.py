"""
PhoneBridge — System Tray Application

Provides the system tray icon with phone discovery status,
mount/unmount controls, and notifications.
"""

import logging
import threading
import webbrowser
from typing import Optional, Callable

import pystray
from pystray import MenuItem, Menu
from PIL import Image, ImageDraw, ImageFont

from .discovery import PhoneScanner, DiscoveredPhone
from .mounter import MountManager, MountInfo, MountError, AuthError
from .config import ConfigManager, PhoneConfig
from .startup import is_startup_enabled, enable_startup, disable_startup

logger = logging.getLogger("phonebridge.tray")


def _ask_password(phone_name: str) -> Optional[str]:
    """
    Show a password input dialog using a native Windows dialog via PowerShell.
    
    Tkinter cannot receive keyboard input when called from pystray's 
    background thread on Windows. Using PowerShell as a subprocess 
    completely avoids this issue since it runs in its own process.
    
    Returns the entered password or None if cancelled.
    """
    import subprocess
    import sys

    if sys.platform != "win32":
        # Fallback for non-Windows (not expected, but safe)
        return input(f"Enter password for {phone_name}: ")

    # PowerShell script that shows a native Windows input dialog
    ps_script = f'''
Add-Type -AssemblyName Microsoft.VisualBasic
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$result = [Microsoft.VisualBasic.Interaction]::InputBox(
    "Enter the connection password displayed on your phone screen.`n`nUsername: phonebridge",
    "PhoneBridge - Connect to {phone_name}",
    ""
)
if ($result) {{ Write-Output $result }} else {{ Write-Output "" }}
'''

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        password = proc.stdout.strip()
        if password:
            return password
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Password dialog timed out")
        return None
    except Exception as e:
        logger.error(f"Password dialog error: {e}")
        return None


class TrayIcon:
    """
    System tray icon for PhoneBridge.
    
    Provides:
    - Status icon (green=connected, yellow=scanning, grey=idle)
    - Right-click menu with phone list, mount/unmount, settings
    - Toast notifications for events
    """

    # Color scheme
    COLOR_CONNECTED = "#4CAF50"   # Green — at least one phone mounted
    COLOR_SCANNING = "#FF9800"    # Orange — scanning, no phones found yet
    COLOR_IDLE = "#9E9E9E"        # Grey — not scanning
    COLOR_ERROR = "#F44336"       # Red — error state

    def __init__(
        self,
        scanner: PhoneScanner,
        mounter: MountManager,
        config: ConfigManager,
        gui=None,
    ):
        self.scanner = scanner
        self.mounter = mounter
        self.config = config
        self._gui = gui

        self._icon: Optional[pystray.Icon] = None
        self._discovered: dict[str, DiscoveredPhone] = {}
        self._lock = threading.Lock()

        # Wire up scanner callbacks
        self.scanner._on_found = self._on_phone_found
        self.scanner._on_lost = self._on_phone_lost

        # Wire up mounter callbacks
        self.mounter._on_mount = self._on_mounted
        self.mounter._on_unmount = self._on_unmounted
        self.mounter._on_error = self._on_mount_error
        self.mounter._on_auth_failed = self._handle_auth_failure

    def start(self):
        """Create and run the system tray icon (blocking)."""
        logger.info("Starting system tray...")

        self._icon = pystray.Icon(
            name="PhoneBridge",
            icon=self._create_icon(self.COLOR_SCANNING),
            title="PhoneBridge — Scanning...",
            menu=self._build_menu(),
        )

        # When running standalone (no GUI), start scanner/mounter here
        if not self._gui:
            threading.Thread(target=self._start_scanner, daemon=True).start()
            self.mounter.start_health_monitor()

        # Run icon (blocks until stop)
        self._icon.run()

    def stop(self):
        """Stop the tray icon and clean up."""
        logger.info("Stopping tray...")
        self.mounter.unmount_all()
        self.mounter.stop_health_monitor()
        self.scanner.stop()
        if self._icon:
            self._icon.stop()

    # ─── Icon Generation ───────────────────────────────────────────

    def _create_icon(self, color: str, badge_count: int = 0) -> Image.Image:
        """Generate a tray icon with the given color and optional badge."""
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Parse hex color
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)

        # Main circle (phone icon base)
        margin = 4
        draw.rounded_rectangle(
            [margin + 8, margin, size - margin - 8, size - margin],
            radius=8,
            fill=(r, g, b, 255),
            outline=(255, 255, 255, 200),
            width=2,
        )

        # Screen area (lighter rectangle inside)
        screen_margin = 10
        draw.rounded_rectangle(
            [margin + screen_margin + 4, margin + screen_margin,
             size - margin - screen_margin - 4, size - margin - screen_margin - 4],
            radius=3,
            fill=(min(r + 60, 255), min(g + 60, 255), min(b + 60, 255), 255),
        )

        # Wi-Fi waves on top
        wave_color = (255, 255, 255, 180)
        cx, cy = size // 2, margin + 6
        for i, radius in enumerate([4, 8, 12]):
            draw.arc(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                start=200,
                end=340,
                fill=wave_color,
                width=2,
            )

        # Badge for phone count
        if badge_count > 0:
            badge_r = 10
            badge_x = size - badge_r - 2
            badge_y = badge_r + 2
            draw.ellipse(
                [badge_x - badge_r, badge_y - badge_r,
                 badge_x + badge_r, badge_y + badge_r],
                fill=(244, 67, 54, 255),
                outline=(255, 255, 255, 255),
                width=1,
            )
            # Number text
            try:
                font = ImageFont.truetype("arial.ttf", 12)
            except (OSError, IOError):
                font = ImageFont.load_default()

            text = str(badge_count)
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(
                (badge_x - tw // 2, badge_y - th // 2 - 1),
                text,
                fill=(255, 255, 255, 255),
                font=font,
            )

        return img

    # ─── Menu Building ─────────────────────────────────────────────

    def _build_menu(self) -> Menu:
        """Build the right-click context menu."""
        
        def make_unmount(did):
            return lambda icon, item: self._unmount_phone(did)
            
        def make_mount(p):
            return lambda icon, item: self._mount_phone(p)
            
        def make_explorer(dl):
            return lambda icon, item: self._open_explorer(dl)

        items = []

        # Header
        items.append(MenuItem("PhoneBridge", None, enabled=False))
        items.append(Menu.SEPARATOR)

        # Discovered phones section
        with self._lock:
            phones = dict(self._discovered)

        if phones:
            for device_id, phone in phones.items():
                is_mounted = self.mounter.is_mounted(device_id)
                mount_info = self.mounter.get_mounts().get(device_id)

                if is_mounted and mount_info:
                    label = f"📱 {phone.display_name} → {mount_info.drive_letter}"
                    auth_label = "🔒 Authenticated" if mount_info.auth_user else "🔓 No auth"
                    protocol_label = f"Protocol: {phone.protocol.upper()}"
                    submenu = Menu(
                        MenuItem(f"Drive: {mount_info.drive_letter}", None, enabled=False),
                        MenuItem(f"IP: {phone.ip_address}:{phone.port}", None, enabled=False),
                        MenuItem(auth_label, None, enabled=False),
                        MenuItem(protocol_label, None, enabled=False),
                        Menu.SEPARATOR,
                        MenuItem("Unmount", make_unmount(device_id)),
                        MenuItem(
                            "Open in Explorer",
                            make_explorer(mount_info.drive_letter),
                        ),
                    )
                else:
                    auth_icon = "🔒" if phone.auth_required else "🔓"
                    label = f"📱 {phone.display_name} {auth_icon}"
                    submenu = Menu(
                        MenuItem(f"IP: {phone.ip_address}:{phone.port}", None, enabled=False),
                        MenuItem(f"Model: {phone.device_model}", None, enabled=False),
                        MenuItem(f"Protocol: {phone.protocol.upper()}", None, enabled=False),
                        Menu.SEPARATOR,
                        MenuItem("Mount as Drive", make_mount(phone)),
                    )

                items.append(MenuItem(label, submenu))
        else:
            items.append(MenuItem("No phones found", None, enabled=False))
            items.append(MenuItem("Make sure PhoneBridge is running on your phone", None, enabled=False))

        items.append(Menu.SEPARATOR)

        # Actions
        mounts = self.mounter.get_mounts()
        if mounts:
            items.append(MenuItem(
                f"Unmount All ({len(mounts)} mounted)",
                lambda: self._unmount_all(),
            ))
            items.append(Menu.SEPARATOR)

        # Settings & Info
        items.append(MenuItem(
            "🔄 Rescan Network",
            lambda: self._rescan(),
        ))

        # Start with Windows toggle
        startup_enabled = is_startup_enabled()
        items.append(MenuItem(
            "⚡ Start with Windows",
            lambda icon, item: self._toggle_startup(),
            checked=lambda item: is_startup_enabled(),
        ))

        dep_status = self._get_dependency_status()
        items.append(MenuItem(
            f"Dependencies: {dep_status}",
            None,
            enabled=False,
        ))

        items.append(Menu.SEPARATOR)

        # Open GUI window
        if self._gui:
            items.insert(0, MenuItem(
                "📱 Open PhoneBridge",
                lambda: self._open_gui(),
            ))
            items.insert(1, Menu.SEPARATOR)

        items.append(MenuItem(
            "📖 GitHub",
            lambda: webbrowser.open("https://github.com/ysachin26/PhoneBridge"),
        ))

        items.append(MenuItem("Quit", lambda: self._quit()))

        return Menu(*items)

    def _refresh_menu(self):
        """Rebuild the menu and update the icon."""
        if self._icon:
            self._icon.menu = self._build_menu()

            # Update icon color and badge
            mounts = self.mounter.get_mounts()
            with self._lock:
                n_discovered = len(self._discovered)

            if mounts:
                self._icon.icon = self._create_icon(self.COLOR_CONNECTED, len(mounts))
                self._icon.title = f"PhoneBridge — {len(mounts)} phone(s) mounted"
            elif n_discovered > 0:
                self._icon.icon = self._create_icon(self.COLOR_SCANNING, n_discovered)
                self._icon.title = f"PhoneBridge — {n_discovered} phone(s) found"
            else:
                self._icon.icon = self._create_icon(self.COLOR_SCANNING)
                self._icon.title = "PhoneBridge — Scanning..."

    def _open_gui(self):
        """Show the GUI window."""
        if self._gui:
            self._gui.after(0, self._gui.show_window)

    def _quit(self):
        """Fully quit the application (tray + GUI)."""
        self.stop()
        # Force exit the process (pywebview may block otherwise)
        import os
        os._exit(0)

    # ─── Actions ───────────────────────────────────────────────────

    def _mount_phone(self, phone: DiscoveredPhone):
        """Mount a phone as a drive letter."""
        try:
            # Get drive letter from config or auto-assign
            phone_config = self.config.get_phone(phone.device_id)
            if phone_config and phone_config.preferred_drive:
                drive_letter = phone_config.preferred_drive
            else:
                drive_letter = self.mounter.get_next_drive_letter()

            if not drive_letter:
                self._notify("No Drive Letters", "No available drive letters. Unmount something first.")
                return

            # Handle authentication
            auth_user = ""
            auth_password = ""

            if phone.auth_required:
                # Check if we have a saved password
                if phone_config and phone_config.auth_password:
                    auth_user = phone_config.auth_user or phone.auth_user
                    auth_password = phone_config.auth_password
                    logger.info(f"Using saved password for {phone.display_name}")

                    # Pre-check: verify saved password still works
                    try:
                        self.mounter.check_auth(
                            phone.webdav_url, auth_user, auth_password
                        )
                    except AuthError:
                        # Saved password is stale — clear it and ask for new one
                        logger.warning(
                            f"Saved password for {phone.display_name} is no longer valid"
                        )
                        self._notify(
                            "Password Changed",
                            f"\U0001f511 {phone.display_name}'s connection code was changed.\n"
                            f"Please enter the new code.",
                        )
                        phone_config.auth_password = ""
                        self.config.upsert_phone(phone_config)
                        auth_password = ""
                    except MountError as e:
                        # Server is unreachable — abort mount entirely
                        logger.warning(f"Server not reachable for {phone.display_name}: {e}")
                        self._notify(
                            "Server Not Running",
                            f"{phone.display_name}'s server is not responding.\n"
                            f"Start PhoneBridge on your phone first.",
                        )
                        return

                if not auth_password:
                    # Prompt for password (first time or after password change)
                    auth_password = _ask_password(phone.display_name)
                    if not auth_password:
                        self._notify("Mount Cancelled", "No password provided.")
                        return
                    auth_user = phone.auth_user

                    # Verify the new password before mounting
                    try:
                        self.mounter.check_auth(
                            phone.webdav_url, auth_user, auth_password
                        )
                    except AuthError:
                        self._notify(
                            "Wrong Password",
                            f"The connection code you entered is incorrect.\n"
                            f"Check the code displayed on {phone.display_name}.",
                        )
                        return
                    except MountError as e:
                        logger.warning(f"Server not reachable for {phone.display_name}: {e}")
                        self._notify(
                            "Server Not Running",
                            f"{phone.display_name}'s server is not responding.",
                        )
                        return

            mount_info = self.mounter.mount(
                phone, drive_letter,
                auth_user=auth_user,
                auth_password=auth_password,
            )

            # Save phone config with credentials
            self.config.upsert_phone(PhoneConfig(
                device_id=phone.device_id,
                display_name=phone.display_name,
                last_ip=phone.ip_address,
                last_port=phone.port,
                preferred_drive=drive_letter,
                auth_user=auth_user,
                auth_password=auth_password,
            ))

            self._refresh_menu()

        except MountError as e:
            logger.error(f"Mount failed: {e}")
            self._notify("Mount Failed", str(e))
        except Exception as e:
            logger.error(f"Unexpected mount error: {e}")
            self._notify("Error", f"Unexpected error: {e}")

    def _unmount_phone(self, device_id: str):
        """Unmount a specific phone."""
        self.mounter.unmount(device_id)
        self._refresh_menu()

    def _unmount_all(self):
        """Unmount all phones."""
        self.mounter.unmount_all()
        self._refresh_menu()

    def _rescan(self):
        """Restart network scanning."""
        logger.info("Manual rescan triggered")
        self.scanner.stop()
        with self._lock:
            self._discovered.clear()
        self._refresh_menu()
        threading.Thread(target=self._start_scanner, daemon=True).start()

    def _open_explorer(self, drive_letter: str):
        """Open a drive letter in Windows Explorer."""
        import subprocess as sp
        try:
            sp.Popen(["explorer.exe", f"{drive_letter}\\"])
        except Exception as e:
            logger.error(f"Failed to open Explorer: {e}")

    def _toggle_startup(self):
        """Toggle the 'Start with Windows' setting."""
        if is_startup_enabled():
            disable_startup()
            self.config.config.start_with_windows = False
            self._notify("Startup Disabled", "PhoneBridge will no longer start with Windows.")
        else:
            enable_startup()
            self.config.config.start_with_windows = True
            self._notify("Startup Enabled", "PhoneBridge will start automatically when Windows starts.")
        self.config.save()
        self._refresh_menu()

    def _start_scanner(self):
        """Start the mDNS scanner."""
        self.scanner.start()

    # ─── Callbacks ─────────────────────────────────────────────────

    def _on_phone_found(self, phone: DiscoveredPhone):
        """Called when a phone is discovered."""
        with self._lock:
            self._discovered[phone.device_id] = phone

        auth_status = "🔒 Auth required" if phone.auth_required else "🔓 Open"
        self._notify("Phone Found", f"📱 {phone.display_name} ({phone.ip_address}) — {auth_status}")
        self._refresh_menu()

        # Auto-mount if enabled
        phone_config = self.config.get_phone(phone.device_id)
        if phone_config and phone_config.auto_mount:
            # Only auto-mount if we have saved credentials (or no auth required)
            if not phone.auth_required or phone_config.auth_password:
                # First verify the server is actually responding
                if not self.mounter.is_server_reachable(phone.webdav_url):
                    logger.info(
                        f"Skipping auto-mount for {phone.display_name} — "
                        f"server not responding (phone app may be off)"
                    )
                    return
                logger.info(f"Auto-mounting {phone.display_name}...")
                threading.Thread(
                    target=self._mount_phone,
                    args=(phone,),
                    daemon=True,
                ).start()
            else:
                logger.info(f"Skipping auto-mount for {phone.display_name} — password not saved")

    def _on_phone_lost(self, device_id: str):
        """Called when a phone disappears from the network."""
        with self._lock:
            lost_phone = self._discovered.pop(device_id, None)

        if lost_phone:
            self._notify("Phone Lost", f"📱 {lost_phone.display_name} disconnected")

        # Unmount if mounted
        if self.mounter.is_mounted(device_id):
            self.mounter.unmount(device_id)

        self._refresh_menu()

    def _on_mounted(self, mount_info: MountInfo):
        """Called when a phone is successfully mounted."""
        self._notify(
            "Phone Mounted",
            f"📱 {mount_info.display_name} → {mount_info.drive_letter}",
        )
        self._refresh_menu()

    def _on_unmounted(self, device_id: str):
        """Called when a phone is unmounted."""
        self._refresh_menu()

    def _on_mount_error(self, device_id: str, error: str):
        """Called when a mount encounters an error."""
        is_auth = MountManager.is_auth_error(error)

        if is_auth:
            # Password was changed on the phone — trigger re-auth flow
            logger.warning(f"Auth failure for {device_id} — starting re-auth flow")
            threading.Thread(
                target=self._handle_auth_failure,
                args=(device_id,),
                daemon=True,
            ).start()
        else:
            self._notify("Mount Error", f"Lost connection: {error[:100]}")

        self._refresh_menu()

    def _handle_auth_failure(self, device_id: str):
        """
        Handle an authentication failure:
        1. Clear the stale saved password
        2. Notify the user that the password changed
        3. Prompt for the new connection code
        4. Re-mount with new credentials
        """
        phone_config = self.config.get_phone(device_id)
        if not phone_config:
            logger.warning(f"No config found for {device_id}, cannot re-auth")
            return

        phone_name = phone_config.display_name

        # 1. Clear stale password
        phone_config.auth_password = ""
        self.config.upsert_phone(phone_config)
        logger.info(f"Cleared stale password for {phone_name}")

        # 2. Notify and prompt
        self._notify(
            "Password Changed",
            f"\U0001f511 {phone_name}'s connection code was changed.\n"
            f"Enter the new code to reconnect.",
        )

        new_password = _ask_password(phone_name)
        if not new_password:
            self._notify(
                "Reconnect Cancelled",
                f"{phone_name} was disconnected. Mount again from the menu.",
            )
            return

        # 3. Look up the discovered phone to re-mount
        with self._lock:
            phone = self._discovered.get(device_id)

        if not phone:
            # Phone may have been lost from mDNS — save the password for next auto-mount
            logger.warning(f"Phone {phone_name} no longer on network, saving password for later")
            phone_config.auth_password = new_password
            self.config.upsert_phone(phone_config)
            self._notify(
                "Phone Not Found",
                f"{phone_name} is not on the network. Will auto-connect when it reappears.",
            )
            return

        # 4. Verify the new password
        try:
            self.mounter.check_auth(
                phone.webdav_url, phone.auth_user, new_password
            )
        except AuthError:
            self._notify(
                "Wrong Password",
                f"The connection code is incorrect. Check {phone_name}'s screen."
            )
            return
        except MountError:
            pass  # Network hiccup — let rclone try

        # 5. Re-mount
        drive_letter = phone_config.preferred_drive or self.mounter.get_next_drive_letter()
        if not drive_letter:
            self._notify("No Drive Letters", "No available drive letters.")
            return

        try:
            self.mounter.mount(
                phone, drive_letter,
                auth_user=phone.auth_user,
                auth_password=new_password,
            )
            # Save new credentials
            phone_config.auth_password = new_password
            phone_config.auth_user = phone.auth_user
            phone_config.preferred_drive = drive_letter
            self.config.upsert_phone(phone_config)
            logger.info(f"\u2705 Re-mounted {phone_name} with new password")
        except MountError as e:
            logger.error(f"Re-mount failed: {e}")
            self._notify("Re-mount Failed", str(e))

    # ─── Helpers ───────────────────────────────────────────────────

    def _notify(self, title: str, message: str):
        """Show a system notification."""
        if not self.config.config.show_notifications:
            return
        try:
            if self._icon and self._icon.HAS_NOTIFICATION:
                self._icon.notify(message, title=title)
        except Exception as e:
            logger.debug(f"Notification failed (non-critical): {e}")

    def _get_dependency_status(self) -> str:
        """Get human-readable dependency status."""
        missing = self.mounter.check_dependencies()
        if not missing:
            return "✅ All OK"
        return f"❌ Missing: {', '.join(missing)}"
