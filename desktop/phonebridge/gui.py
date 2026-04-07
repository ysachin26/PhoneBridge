"""
PhoneBridge — Desktop GUI (customtkinter)

Native desktop window for managing phone connections, mounts, and settings.
"""

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Optional

import customtkinter as ctk

from .config import ConfigManager, PhoneConfig
from .discovery import PhoneScanner, DiscoveredPhone
from .mounter import MountManager, MountInfo, MountError, AuthError
from .startup import is_startup_enabled, enable_startup, disable_startup
from .utils import check_rclone, check_winfsp
from . import __version__

logger = logging.getLogger("phonebridge.gui")

# ─── Theme ─────────────────────────────────────────────────

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COLOR_BG = "#0d1117"
COLOR_CARD = "#161b22"
COLOR_CARD_BORDER = "#30363d"
COLOR_ACCENT = "#6366f1"
COLOR_GREEN = "#22c55e"
COLOR_ORANGE = "#f59e0b"
COLOR_RED = "#ef4444"
COLOR_TEXT = "#e6edf3"
COLOR_TEXT_SEC = "#8b949e"
COLOR_MOUNTED = "#1a3a2a"
COLOR_MOUNTED_BORDER = "#2ea043"


class DeviceCard(ctk.CTkFrame):
    """Card widget for a single discovered phone."""

    def __init__(self, master, phone: DiscoveredPhone, mount_info: Optional[MountInfo],
                 on_mount, on_unmount, on_explorer, on_change_pass, **kwargs):
        super().__init__(
            master,
            fg_color=COLOR_MOUNTED if mount_info and mount_info.is_alive else COLOR_CARD,
            border_color=COLOR_MOUNTED_BORDER if mount_info and mount_info.is_alive else COLOR_CARD_BORDER,
            border_width=1,
            corner_radius=12,
            **kwargs,
        )
        self.phone = phone
        self.mount_info = mount_info
        self._on_mount = on_mount
        self._on_unmount = on_unmount
        self._on_explorer = on_explorer
        self._on_change_pass = on_change_pass

        self._build()

    def _build(self):
        is_mounted = self.mount_info and self.mount_info.is_alive
        pad = 16

        # ─── Top Row: Name + Badge ────────────────────────
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.pack(fill="x", padx=pad, pady=(pad, 4))

        name_label = ctk.CTkLabel(
            top_frame,
            text=self.phone.display_name,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLOR_TEXT,
            anchor="w",
        )
        name_label.pack(side="left")

        if is_mounted:
            badge_text = f"Mounted ({self.mount_info.drive_letter})"
            badge_fg = "#122a1a"
            badge_color = COLOR_GREEN
        else:
            badge_text = "Discovered"
            badge_fg = "#2a2008"
            badge_color = COLOR_ORANGE

        badge = ctk.CTkLabel(
            top_frame,
            text=badge_text,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=badge_color,
            fg_color=badge_fg,
            corner_radius=12,
            padx=10,
            pady=2,
        )
        badge.pack(side="right")

        # ─── Details Row ──────────────────────────────────
        details_frame = ctk.CTkFrame(self, fg_color="transparent")
        details_frame.pack(fill="x", padx=pad, pady=(0, 8))

        details = [
            f"IP: {self.phone.ip_address}:{self.phone.port}",
            self.phone.protocol.upper(),
            "Auth Required" if self.phone.auth_required else "Open",
        ]
        if hasattr(self.phone, 'device_model') and self.phone.device_model:
            details.append(self.phone.device_model)
        if is_mounted:
            details.append(f"Drive {self.mount_info.drive_letter}")

        detail_text = "  |  ".join(details)
        detail_label = ctk.CTkLabel(
            details_frame,
            text=detail_text,
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SEC,
            anchor="w",
        )
        detail_label.pack(side="left")

        # ─── Action Buttons ───────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=pad, pady=(4, pad))

        if is_mounted:
            ctk.CTkButton(
                btn_frame, text="Open Explorer", width=130,
                fg_color="#1a3a2a", hover_color="#2ea043",
                border_color="#2ea043", border_width=1,
                font=ctk.CTkFont(size=13),
                command=lambda: self._on_explorer(self.phone.device_id),
            ).pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                btn_frame, text="Unmount", width=110,
                fg_color="#3a1a1a", hover_color="#da3633",
                border_color="#da3633", border_width=1,
                font=ctk.CTkFont(size=13),
                command=lambda: self._on_unmount(self.phone.device_id),
            ).pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                btn_frame, text="Change Password", width=150,
                fg_color="transparent", hover_color="#30363d",
                border_color=COLOR_CARD_BORDER, border_width=1,
                font=ctk.CTkFont(size=13),
                command=lambda: self._on_change_pass(self.phone),
            ).pack(side="left")
        else:
            if self.phone.auth_required:
                ctk.CTkButton(
                    btn_frame, text="Enter Code & Mount", width=180,
                    fg_color=COLOR_ACCENT, hover_color="#818cf8",
                    font=ctk.CTkFont(size=13, weight="bold"),
                    command=lambda: self._on_mount(self.phone),
                ).pack(side="left")
            else:
                ctk.CTkButton(
                    btn_frame, text="Mount Drive", width=140,
                    fg_color=COLOR_ACCENT, hover_color="#818cf8",
                    font=ctk.CTkFont(size=13, weight="bold"),
                    command=lambda: self._on_mount(self.phone),
                ).pack(side="left")


class PasswordDialog(ctk.CTkToplevel):
    """Modal dialog for entering the phone connection code."""

    def __init__(self, master, phone_name: str, on_submit):
        super().__init__(master)
        self.title("Enter Connection Code")
        self.geometry("420x220")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_BG)
        self._on_submit = on_submit
        self.result = None

        # Center on parent
        self.transient(master)
        self.grab_set()

        # Title
        ctk.CTkLabel(
            self, text="🔑  Enter Connection Code",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(padx=24, pady=(24, 8), anchor="w")

        # Prompt
        ctk.CTkLabel(
            self,
            text=f"Enter the code displayed on {phone_name}",
            font=ctk.CTkFont(size=13),
            text_color=COLOR_TEXT_SEC,
        ).pack(padx=24, pady=(0, 12), anchor="w")

        # Input
        self.entry = ctk.CTkEntry(
            self, placeholder_text="Connection code",
            font=ctk.CTkFont(size=18), height=44,
            justify="center",
        )
        self.entry.pack(padx=24, fill="x")
        self.entry.focus()
        self.entry.bind("<Return>", lambda e: self._submit())

        # Error label (hidden initially)
        self.error_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12),
            text_color=COLOR_RED,
        )
        self.error_label.pack(padx=24, pady=(4, 0), anchor="w")

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(12, 24))

        ctk.CTkButton(
            btn_frame, text="Cancel", width=100,
            fg_color="transparent", hover_color="#30363d",
            border_color=COLOR_CARD_BORDER, border_width=1,
            command=self._cancel,
        ).pack(side="right", padx=(8, 0))

        self.submit_btn = ctk.CTkButton(
            btn_frame, text="Connect", width=100,
            fg_color=COLOR_ACCENT, hover_color="#818cf8",
            command=self._submit,
        )
        self.submit_btn.pack(side="right")

    def _submit(self):
        password = self.entry.get().strip()
        if password:
            self._on_submit(password, self)

    def _cancel(self):
        self.destroy()

    def show_error(self, msg):
        self.error_label.configure(text=msg)
        self.entry.configure(border_color=COLOR_RED)
        self.entry.select_range(0, "end")


class PhoneBridgeApp(ctk.CTk):
    """Main application window."""

    def __init__(self, scanner: PhoneScanner, mounter: MountManager, config: ConfigManager):
        super().__init__()

        self.scanner = scanner
        self.mounter = mounter
        self.config = config

        # Window setup
        self.title(f"PhoneBridge v{__version__}")
        self.geometry("800x580")
        self.minsize(600, 400)
        self.configure(fg_color=COLOR_BG)

        # Handle close → hide to tray
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._start_polling()

    def _build_ui(self):
        # ─── Header ───────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="#010409", height=56, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="📱  PhoneBridge",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(side="left", padx=20)

        ctk.CTkLabel(
            header, text=f"v{__version__}",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_SEC,
        ).pack(side="left", padx=(4, 0), pady=(4, 0))

        # Settings button
        ctk.CTkButton(
            header, text="⚙ Settings", width=90,
            fg_color="transparent", hover_color="#30363d",
            border_color=COLOR_CARD_BORDER, border_width=1,
            font=ctk.CTkFont(size=12),
            command=self._open_settings,
        ).pack(side="right", padx=(0, 12))

        # Rescan button
        ctk.CTkButton(
            header, text="🔄 Rescan", width=90,
            fg_color="transparent", hover_color="#30363d",
            border_color=COLOR_CARD_BORDER, border_width=1,
            font=ctk.CTkFont(size=12),
            command=self._rescan,
        ).pack(side="right", padx=(0, 8))

        # ─── Status Bar ──────────────────────────────────
        self.status_frame = ctk.CTkFrame(self, fg_color="#0d1117", height=36, corner_radius=0)
        self.status_frame.pack(fill="x")
        self.status_frame.pack_propagate(False)

        self.scan_indicator = ctk.CTkLabel(
            self.status_frame, text="●",
            font=ctk.CTkFont(size=14),
            text_color=COLOR_GREEN,
        )
        self.scan_indicator.pack(side="left", padx=(20, 6))

        self.status_label = ctk.CTkLabel(
            self.status_frame, text="Scanning...",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SEC,
        )
        self.status_label.pack(side="left")

        self.device_count_label = ctk.CTkLabel(
            self.status_frame, text="  •  0 devices",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SEC,
        )
        self.device_count_label.pack(side="left")

        self.mount_count_label = ctk.CTkLabel(
            self.status_frame, text="  •  0 mounted",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SEC,
        )
        self.mount_count_label.pack(side="left")

        # Deps status
        self.deps_label = ctk.CTkLabel(
            self.status_frame, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SEC,
        )
        self.deps_label.pack(side="right", padx=20)
        self._check_deps()

        # ─── Scrollable Device List ───────────────────────
        self.device_scroll = ctk.CTkScrollableFrame(
            self, fg_color=COLOR_BG,
            scrollbar_button_color="#30363d",
            scrollbar_button_hover_color="#484f58",
        )
        self.device_scroll.pack(fill="both", expand=True, padx=16, pady=16)

        # Empty state
        self.empty_frame = ctk.CTkFrame(self.device_scroll, fg_color="transparent")
        self.empty_label_icon = ctk.CTkLabel(
            self.empty_frame, text="📡",
            font=ctk.CTkFont(size=48),
            text_color=COLOR_TEXT_SEC,
        )
        self.empty_label_icon.pack(pady=(60, 8))

        self.empty_label = ctk.CTkLabel(
            self.empty_frame,
            text="Scanning for phones on your network...",
            font=ctk.CTkFont(size=15),
            text_color=COLOR_TEXT_SEC,
        )
        self.empty_label.pack()

        self.empty_hint = ctk.CTkLabel(
            self.empty_frame,
            text="Make sure PhoneBridge is running on your Android device",
            font=ctk.CTkFont(size=12),
            text_color="#484f58",
        )
        self.empty_hint.pack(pady=(4, 0))

        self.empty_frame.pack(fill="both", expand=True)

    def _check_deps(self):
        rclone_ok = bool(check_rclone())
        winfsp_ok = check_winfsp() if sys.platform == "win32" else True
        if rclone_ok and winfsp_ok:
            self.deps_label.configure(text="📦 Dependencies: All OK", text_color=COLOR_GREEN)
        else:
            missing = []
            if not rclone_ok:
                missing.append("rclone")
            if not winfsp_ok:
                missing.append("WinFsp")
            self.deps_label.configure(
                text=f"⚠ Missing: {', '.join(missing)}",
                text_color=COLOR_RED,
            )

    # ─── Polling & Refresh ────────────────────────────────────

    def _start_polling(self):
        """Poll for device changes every 2 seconds."""
        self._refresh_devices()
        self.after(2000, self._start_polling)

    def _refresh_devices(self):
        """Rebuild the device card list."""
        phones = self.scanner.get_phones()
        mounts = self.mounter.get_mounts()

        # Update status bar
        n_devices = len(phones)
        n_mounts = len(mounts)
        self.device_count_label.configure(text=f"  •  {n_devices} device{'s' if n_devices != 1 else ''}")
        self.mount_count_label.configure(text=f"  •  {n_mounts} mounted")

        if n_mounts > 0:
            self.status_label.configure(text="Connected")
            self.scan_indicator.configure(text_color=COLOR_GREEN)
        elif n_devices > 0:
            self.status_label.configure(text="Devices found")
            self.scan_indicator.configure(text_color=COLOR_ORANGE)
        else:
            self.status_label.configure(text="Scanning...")
            self.scan_indicator.configure(text_color=COLOR_TEXT_SEC)

        # Clear existing cards
        for widget in self.device_scroll.winfo_children():
            widget.destroy()

        if not phones:
            # Show empty state
            empty = ctk.CTkFrame(self.device_scroll, fg_color="transparent")
            ctk.CTkLabel(empty, text="📡", font=ctk.CTkFont(size=48), text_color=COLOR_TEXT_SEC).pack(pady=(60, 8))
            ctk.CTkLabel(empty, text="Scanning for phones on your network...",
                         font=ctk.CTkFont(size=15), text_color=COLOR_TEXT_SEC).pack()
            ctk.CTkLabel(empty, text="Make sure PhoneBridge is running on your Android device",
                         font=ctk.CTkFont(size=12), text_color="#484f58").pack(pady=(4, 0))
            empty.pack(fill="both", expand=True)
            return

        # Create device cards
        for device_id, phone in phones.items():
            try:
                mount = mounts.get(device_id)
                card = DeviceCard(
                    self.device_scroll,
                    phone=phone,
                    mount_info=mount,
                    on_mount=self._handle_mount,
                    on_unmount=self._handle_unmount,
                    on_explorer=self._handle_explorer,
                    on_change_pass=self._handle_change_pass,
                )
                card.pack(fill="x", pady=(0, 10))
            except Exception as e:
                logger.error(f"Failed to render card for {phone.display_name}: {e}")
                # Fallback: show a simple label
                fallback = ctk.CTkLabel(
                    self.device_scroll,
                    text=f"  {phone.display_name} — {phone.ip_address}:{phone.port}",
                    font=ctk.CTkFont(size=14),
                    text_color=COLOR_TEXT,
                    anchor="w",
                )
                fallback.pack(fill="x", pady=4)

    # ─── Actions ──────────────────────────────────────────────

    def _handle_mount(self, phone: DiscoveredPhone):
        """Mount a phone — prompt for password if auth required."""
        if phone.auth_required:
            # Check if we have a saved password that still works
            phone_config = self.config.get_phone(phone.device_id)
            if phone_config and phone_config.auth_password:
                # Try saved password first
                threading.Thread(
                    target=self._try_mount_with_saved_password,
                    args=(phone, phone_config),
                    daemon=True,
                ).start()
            else:
                self._show_password_dialog(phone)
        else:
            threading.Thread(
                target=self._do_mount,
                args=(phone, "", ""),
                daemon=True,
            ).start()

    def _try_mount_with_saved_password(self, phone, phone_config):
        auth_user = phone_config.auth_user or phone.auth_user
        password = phone_config.auth_password
        try:
            self.mounter.check_auth(phone.webdav_url, auth_user, password)
            self._do_mount(phone, auth_user, password)
        except AuthError:
            # Saved password is stale
            self.after(0, lambda: self._show_password_dialog(phone, "Saved password expired. Enter the new code."))
        except MountError as e:
            self.after(0, lambda: self._show_error("Server Not Running",
                f"{phone.display_name}'s server is not responding.\nStart PhoneBridge on your phone first."))

    def _show_password_dialog(self, phone, error_msg=""):
        dialog = PasswordDialog(
            self,
            phone_name=phone.display_name,
            on_submit=lambda pw, dlg: self._on_password_submitted(phone, pw, dlg),
        )
        if error_msg:
            dialog.show_error(error_msg)

    def _on_password_submitted(self, phone, password, dialog):
        """Verify password and mount."""
        auth_user = phone.auth_user or "phonebridge"

        def do_verify():
            try:
                self.mounter.check_auth(phone.webdav_url, auth_user, password)
                self.after(0, dialog.destroy)
                self._do_mount(phone, auth_user, password)
            except AuthError:
                self.after(0, lambda: dialog.show_error("Incorrect code. Check your phone."))
            except MountError as e:
                self.after(0, lambda: dialog.show_error(f"Server not reachable: {e}"))

        threading.Thread(target=do_verify, daemon=True).start()

    def _do_mount(self, phone, auth_user, password):
        """Actually perform the mount (runs in background thread)."""
        try:
            phone_config = self.config.get_phone(phone.device_id)
            if phone_config and phone_config.preferred_drive:
                drive_letter = phone_config.preferred_drive
            else:
                drive_letter = self.mounter.get_next_drive_letter()

            if not drive_letter:
                self.after(0, lambda: self._show_error("No Drives", "No available drive letters."))
                return

            mount_info = self.mounter.mount(
                phone, drive_letter,
                auth_user=auth_user,
                auth_password=password,
            )

            # Save config
            self.config.upsert_phone(PhoneConfig(
                device_id=phone.device_id,
                display_name=phone.display_name,
                last_ip=phone.ip_address,
                last_port=phone.port,
                preferred_drive=drive_letter,
                auth_user=auth_user,
                auth_password=password,
            ))

        except MountError as e:
            self.after(0, lambda: self._show_error("Mount Failed", str(e)))

    def _handle_unmount(self, device_id: str):
        try:
            self.mounter.unmount(device_id)
        except Exception as e:
            self._show_error("Unmount Failed", str(e))

    def _handle_explorer(self, device_id: str):
        mounts = self.mounter.get_mounts()
        mount = mounts.get(device_id)
        if mount and mount.is_alive:
            subprocess.Popen(["explorer.exe", mount.drive_letter])

    def _handle_change_pass(self, phone: DiscoveredPhone):
        self._show_password_dialog(phone)

    # ─── Settings ─────────────────────────────────────────────

    def _open_settings(self):
        settings_win = ctk.CTkToplevel(self)
        settings_win.title("PhoneBridge Settings")
        settings_win.geometry("400x350")
        settings_win.configure(fg_color=COLOR_BG)
        settings_win.transient(self)
        settings_win.grab_set()

        ctk.CTkLabel(
            settings_win, text="⚙  Settings",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(padx=24, pady=(24, 16), anchor="w")

        # Start with Windows
        startup_var = ctk.BooleanVar(value=is_startup_enabled())
        startup_frame = ctk.CTkFrame(settings_win, fg_color=COLOR_CARD, corner_radius=10)
        startup_frame.pack(fill="x", padx=24, pady=(0, 8))
        ctk.CTkLabel(startup_frame, text="Start with Windows",
                     font=ctk.CTkFont(size=14), text_color=COLOR_TEXT).pack(side="left", padx=16, pady=12)
        ctk.CTkSwitch(startup_frame, text="", variable=startup_var,
                      command=lambda: enable_startup() if startup_var.get() else disable_startup(),
                      onvalue=True, offvalue=False).pack(side="right", padx=16, pady=12)

        # Notifications
        notif_var = ctk.BooleanVar(value=self.config.config.show_notifications)
        notif_frame = ctk.CTkFrame(settings_win, fg_color=COLOR_CARD, corner_radius=10)
        notif_frame.pack(fill="x", padx=24, pady=(0, 8))
        ctk.CTkLabel(notif_frame, text="Show Notifications",
                     font=ctk.CTkFont(size=14), text_color=COLOR_TEXT).pack(side="left", padx=16, pady=12)
        ctk.CTkSwitch(notif_frame, text="", variable=notif_var,
                      command=lambda: self._set_notifications(notif_var.get()),
                      onvalue=True, offvalue=False).pack(side="right", padx=16, pady=12)

        # Cache mode
        cache_frame = ctk.CTkFrame(settings_win, fg_color=COLOR_CARD, corner_radius=10)
        cache_frame.pack(fill="x", padx=24, pady=(0, 8))
        ctk.CTkLabel(cache_frame, text="VFS Cache",
                     font=ctk.CTkFont(size=14), text_color=COLOR_TEXT).pack(side="left", padx=16, pady=12)
        cache_menu = ctk.CTkOptionMenu(
            cache_frame, values=["off", "minimal", "writes", "full"],
            command=self._set_cache_mode,
        )
        cache_menu.set(self.config.config.vfs_cache_mode)
        cache_menu.pack(side="right", padx=16, pady=12)

        # Dependencies
        ctk.CTkLabel(settings_win, text="Dependencies",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLOR_TEXT_SEC).pack(padx=24, pady=(16, 8), anchor="w")

        deps_frame = ctk.CTkFrame(settings_win, fg_color=COLOR_CARD, corner_radius=10)
        deps_frame.pack(fill="x", padx=24)

        rclone_ok = bool(check_rclone())
        winfsp_ok = check_winfsp() if sys.platform == "win32" else True

        for name, installed in [("rclone", rclone_ok), ("WinFsp", winfsp_ok)]:
            row = ctk.CTkFrame(deps_frame, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=6)
            ctk.CTkLabel(row, text=f"{'✅' if installed else '❌'}  {name}",
                         font=ctk.CTkFont(size=13),
                         text_color=COLOR_GREEN if installed else COLOR_RED).pack(side="left")
            ctk.CTkLabel(row, text="Installed" if installed else "Not found",
                         font=ctk.CTkFont(size=12),
                         text_color=COLOR_TEXT_SEC).pack(side="right")

    def _set_notifications(self, enabled):
        self.config.config.show_notifications = enabled
        self.config.save()

    def _set_cache_mode(self, mode):
        self.config.config.vfs_cache_mode = mode
        self.config.save()

    def _rescan(self):
        self.scanner.stop()
        self.scanner.start()

    # ─── Utility ──────────────────────────────────────────────

    def _show_error(self, title, message):
        """Show an error popup."""
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("380x160")
        dialog.configure(fg_color=COLOR_BG)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text=f"⚠  {title}",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=COLOR_RED).pack(padx=24, pady=(20, 8), anchor="w")
        ctk.CTkLabel(dialog, text=message,
                     font=ctk.CTkFont(size=13),
                     text_color=COLOR_TEXT_SEC, wraplength=330).pack(padx=24, anchor="w")
        ctk.CTkButton(dialog, text="OK", width=80,
                      command=dialog.destroy).pack(pady=(16, 20))

    def _on_close(self):
        """Hide to tray instead of quitting."""
        self.withdraw()

    def show_window(self):
        """Show the window (bring from tray)."""
        self.deiconify()
        self.lift()
        self.focus_force()
