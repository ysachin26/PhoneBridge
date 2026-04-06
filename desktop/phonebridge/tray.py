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
from .mounter import MountManager, MountInfo, MountError
from .config import ConfigManager, PhoneConfig

logger = logging.getLogger("phonebridge.tray")


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
    ):
        self.scanner = scanner
        self.mounter = mounter
        self.config = config

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

    def start(self):
        """Create and run the system tray icon (blocking)."""
        logger.info("Starting system tray...")

        self._icon = pystray.Icon(
            name="PhoneBridge",
            icon=self._create_icon(self.COLOR_SCANNING),
            title="PhoneBridge — Scanning...",
            menu=self._build_menu(),
        )

        # Start scanner in background
        threading.Thread(target=self._start_scanner, daemon=True).start()

        # Start health monitor
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
                    action = lambda _, did=device_id: self._unmount_phone(did)
                    submenu = Menu(
                        MenuItem(f"Drive: {mount_info.drive_letter}", None, enabled=False),
                        MenuItem(f"IP: {phone.ip_address}:{phone.port}", None, enabled=False),
                        Menu.SEPARATOR,
                        MenuItem("Unmount", lambda _, did=device_id: self._unmount_phone(did)),
                        MenuItem(
                            "Open in Explorer",
                            lambda _, dl=mount_info.drive_letter: self._open_explorer(dl),
                        ),
                    )
                else:
                    label = f"📱 {phone.display_name}"
                    submenu = Menu(
                        MenuItem(f"IP: {phone.ip_address}:{phone.port}", None, enabled=False),
                        MenuItem(f"Model: {phone.device_model}", None, enabled=False),
                        Menu.SEPARATOR,
                        MenuItem("Mount as Drive", lambda _, p=phone: self._mount_phone(p)),
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

        dep_status = self._get_dependency_status()
        items.append(MenuItem(
            f"Dependencies: {dep_status}",
            None,
            enabled=False,
        ))

        items.append(Menu.SEPARATOR)

        items.append(MenuItem(
            "📖 GitHub",
            lambda: webbrowser.open("https://github.com/ysachin26/PhoneBridge"),
        ))

        items.append(MenuItem("Quit", lambda: self.stop()))

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

            mount_info = self.mounter.mount(phone, drive_letter)

            # Save phone config
            self.config.upsert_phone(PhoneConfig(
                device_id=phone.device_id,
                display_name=phone.display_name,
                last_ip=phone.ip_address,
                last_port=phone.port,
                preferred_drive=drive_letter,
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

    def _start_scanner(self):
        """Start the mDNS scanner."""
        self.scanner.start()

    # ─── Callbacks ─────────────────────────────────────────────────

    def _on_phone_found(self, phone: DiscoveredPhone):
        """Called when a phone is discovered."""
        with self._lock:
            self._discovered[phone.device_id] = phone

        self._notify("Phone Found", f"📱 {phone.display_name} ({phone.ip_address})")
        self._refresh_menu()

        # Auto-mount if enabled
        phone_config = self.config.get_phone(phone.device_id)
        if phone_config and phone_config.auto_mount:
            logger.info(f"Auto-mounting {phone.display_name}...")
            threading.Thread(
                target=self._mount_phone,
                args=(phone,),
                daemon=True,
            ).start()

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
        self._notify("Mount Error", f"Lost connection: {error[:100]}")
        self._refresh_menu()

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
