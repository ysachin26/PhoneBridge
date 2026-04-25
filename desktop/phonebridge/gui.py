"""
PhoneBridge — Desktop GUI (customtkinter)

Native desktop window for managing phone connections, mounts, and settings.
"""

import json
import logging
import os
import subprocess
import ssl
import sys
import threading
import time
import urllib.request
import webbrowser
from typing import Optional

import customtkinter as ctk

from .config import ConfigManager, PhoneConfig
from .discovery import PhoneScanner, DiscoveredPhone
from .mounter import MountManager, MountInfo, MountError, AuthError
from .startup import is_startup_enabled, enable_startup, disable_startup
from .utils import check_rclone, check_winfsp, format_size
from .certpin import get_server_fingerprint, verify_fingerprint, fingerprint_changed
from . import __version__

logger = logging.getLogger("phonebridge.gui")

# ─── Constants ─────────────────────────────────────────────

MOUNT_FOLDER_OPTIONS = [
    ("All Storage", ""),
    ("DCIM (Camera)", "DCIM"),
    ("Downloads", "Download"),
    ("Pictures", "Pictures"),
    ("Music", "Music"),
    ("Movies", "Movies"),
    ("Documents", "Documents"),
]

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
                 on_mount, on_unmount, on_explorer, on_change_pass,
                 on_remove=None, on_toggle_automount=None,
                 phone_config=None, phone_status=None, **kwargs):
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
        self._on_remove = on_remove
        self._on_toggle_automount = on_toggle_automount
        self._phone_config = phone_config
        self._phone_status = phone_status

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

        # Connection type badge
        conn_type = getattr(self.phone, 'connection_type', 'auto')
        if conn_type != "auto":
            conn_colors = {
                "manual": ("#f59e0b", "#2a2008"),   # Orange
                "tailscale": ("#3b82f6", "#0f1d3a"),  # Blue
            }
            clr, bg = conn_colors.get(conn_type, (COLOR_TEXT_SEC, COLOR_CARD))
            ctk.CTkLabel(
                top_frame,
                text=conn_type.capitalize(),
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=clr,
                fg_color=bg,
                corner_radius=8,
                padx=8,
                pady=2,
            ).pack(side="right", padx=(0, 6))

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
        # Connection type indicator for non-LAN connections
        conn_type = getattr(self.phone, 'connection_type', 'auto')
        if conn_type == "manual":
            details.append("Manual")
        elif conn_type == "tailscale":
            details.append("Tailscale")

        detail_text = "  |  ".join(details)
        detail_label = ctk.CTkLabel(
            details_frame,
            text=detail_text,
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SEC,
            anchor="w",
        )
        detail_label.pack(side="left")

        # ─── Storage Info (from REST API) ────────────────
        if self._phone_status and self._phone_status.get("storage_total", 0) > 0:
            storage_frame = ctk.CTkFrame(self, fg_color="transparent")
            storage_frame.pack(fill="x", padx=pad, pady=(0, 6))

            total = self._phone_status["storage_total"]
            used = self._phone_status["storage_used"]
            pct = int((used / total) * 100) if total > 0 else 0
            pct_color = COLOR_GREEN if pct < 75 else (COLOR_ORANGE if pct < 90 else COLOR_RED)

            storage_bar = ctk.CTkProgressBar(
                storage_frame, width=200, height=8,
                progress_color=pct_color,
                fg_color="#21262d",
            )
            storage_bar.set(pct / 100.0)
            storage_bar.pack(side="left", padx=(0, 10))

            ctk.CTkLabel(
                storage_frame,
                text=f"{format_size(used)} / {format_size(total)} ({pct}%)",
                font=ctk.CTkFont(size=11),
                text_color=COLOR_TEXT_SEC,
            ).pack(side="left")

        # ─── Live Transfer Stats (for mounted cards) ──────
        if is_mounted and self._phone_status:
            stats_frame = ctk.CTkFrame(self, fg_color="transparent")
            stats_frame.pack(fill="x", padx=pad, pady=(0, 6))

            uptime_s = self._phone_status.get("uptime_seconds", 0)
            uptime_min = int(uptime_s / 60)
            uptime_text = f"{uptime_min}m" if uptime_min < 60 else f"{uptime_min // 60}h {uptime_min % 60}m"

            active_conn = self._phone_status.get("active_connections", 0)
            bytes_served = self._phone_status.get("bytes_served", 0)
            bytes_received = self._phone_status.get("bytes_received", 0)

            stats_items = [
                f"⬆ {format_size(bytes_served)}",
                f"⬇ {format_size(bytes_received)}",
                f"⏱ {uptime_text}",
            ]
            if active_conn > 0:
                stats_items.append(f"🔄 {active_conn} active")

            ctk.CTkLabel(
                stats_frame,
                text="  ·  ".join(stats_items),
                font=ctk.CTkFont(size=11),
                text_color="#58a6ff",
                anchor="w",
            ).pack(side="left")

            # Show mount path if not root
            if self.mount_info and self.mount_info.mount_path:
                ctk.CTkLabel(
                    stats_frame,
                    text=f"📁 /{self.mount_info.mount_path}",
                    font=ctk.CTkFont(size=11),
                    text_color=COLOR_ORANGE,
                ).pack(side="right")

        # ─── Action Buttons ───────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=pad, pady=(4, 8))

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

        # ─── Bottom Row: Auto-mount Toggle + Remove ───────
        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.pack(fill="x", padx=pad, pady=(0, pad))

        auto_mount_val = True
        if self._phone_config:
            auto_mount_val = self._phone_config.auto_mount

        auto_var = ctk.BooleanVar(value=auto_mount_val)
        ctk.CTkCheckBox(
            bottom_frame, text="Auto-mount",
            variable=auto_var,
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SEC,
            command=lambda: self._on_toggle_automount(self.phone.device_id, auto_var.get()) if self._on_toggle_automount else None,
            checkbox_width=18, checkbox_height=18,
        ).pack(side="left")

        if self._on_remove:
            ctk.CTkButton(
                bottom_frame, text="🗑 Forget", width=80,
                fg_color="transparent", hover_color="#3a1a1a",
                text_color=COLOR_TEXT_SEC,
                font=ctk.CTkFont(size=11),
                command=lambda: self._on_remove(self.phone.device_id),
            ).pack(side="right")


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


class ManualConnectDialog(ctk.CTkToplevel):
    """Dialog for manually connecting to a phone by IP address."""

    def __init__(self, master, on_connect):
        super().__init__(master)
        self.title("Connect Manually")
        self.geometry("440x360")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_BG)
        self._on_connect = on_connect

        self.transient(master)
        self.grab_set()

        # Title
        ctk.CTkLabel(
            self, text="🌐  Connect by IP Address",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(padx=24, pady=(24, 4), anchor="w")

        ctk.CTkLabel(
            self,
            text="Connect to a phone on any network (e.g., via Tailscale VPN)",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SEC,
        ).pack(padx=24, pady=(0, 16), anchor="w")

        # Address field
        ctk.CTkLabel(
            self, text="Address",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(padx=24, anchor="w")
        self.address_entry = ctk.CTkEntry(
            self, placeholder_text="IP or hostname (e.g., 100.64.0.2)",
            font=ctk.CTkFont(size=14), height=38,
        )
        self.address_entry.pack(padx=24, fill="x", pady=(4, 12))
        self.address_entry.focus()

        # Port + Protocol row
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=(0, 12))

        # Port
        port_frame = ctk.CTkFrame(row, fg_color="transparent")
        port_frame.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkLabel(port_frame, text="Port",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=COLOR_TEXT).pack(anchor="w")
        self.port_entry = ctk.CTkEntry(
            port_frame, placeholder_text="8273",
            font=ctk.CTkFont(size=14), height=38,
        )
        self.port_entry.insert(0, "8273")
        self.port_entry.pack(fill="x", pady=(4, 0))

        # Protocol
        proto_frame = ctk.CTkFrame(row, fg_color="transparent")
        proto_frame.pack(side="left", fill="x", expand=True, padx=(8, 0))
        ctk.CTkLabel(proto_frame, text="Protocol",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=COLOR_TEXT).pack(anchor="w")
        self.protocol_menu = ctk.CTkOptionMenu(
            proto_frame, values=["https", "http"],
            height=38, font=ctk.CTkFont(size=14),
        )
        self.protocol_menu.set("https")
        self.protocol_menu.pack(fill="x", pady=(4, 0))

        # Password
        ctk.CTkLabel(
            self, text="Connection Code",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(padx=24, anchor="w")
        self.password_entry = ctk.CTkEntry(
            self, placeholder_text="Code from your phone",
            font=ctk.CTkFont(size=14), height=38,
        )
        self.password_entry.pack(padx=24, fill="x", pady=(4, 8))
        self.password_entry.bind("<Return>", lambda e: self._submit())

        # Error label
        self.error_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12),
            text_color=COLOR_RED,
        )
        self.error_label.pack(padx=24, anchor="w")

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(8, 24))

        ctk.CTkButton(
            btn_frame, text="Cancel", width=100,
            fg_color="transparent", hover_color="#30363d",
            border_color=COLOR_CARD_BORDER, border_width=1,
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))

        self.connect_btn = ctk.CTkButton(
            btn_frame, text="Connect", width=100,
            fg_color=COLOR_ACCENT, hover_color="#818cf8",
            command=self._submit,
        )
        self.connect_btn.pack(side="right")

    def _submit(self):
        address = self.address_entry.get().strip()
        port_str = self.port_entry.get().strip()
        protocol = self.protocol_menu.get()
        password = self.password_entry.get().strip()

        if not address:
            self.show_error("Enter an IP address or hostname")
            return
        try:
            port = int(port_str) if port_str else 8273
            if port < 1 or port > 65535:
                raise ValueError()
        except ValueError:
            self.show_error("Invalid port number")
            return
        if not password:
            self.show_error("Enter the connection code from your phone")
            return

        self.connect_btn.configure(state="disabled", text="Connecting...")
        self._on_connect(address, port, protocol, password, self)

    def show_error(self, msg):
        self.error_label.configure(text=msg)
        self.connect_btn.configure(state="normal", text="Connect")


class FolderPickerDialog(ctk.CTkToplevel):
    """Dialog to select which folder to mount."""

    def __init__(self, master, phone_name: str, on_select):
        super().__init__(master)
        self.title("Select Folder to Mount")
        self.geometry("380x400")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_BG)
        self._on_select = on_select
        self.result = ""

        self.transient(master)
        self.grab_set()

        ctk.CTkLabel(
            self, text="📂  Choose Mount Folder",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(padx=24, pady=(24, 4), anchor="w")

        ctk.CTkLabel(
            self,
            text=f"Which folder from {phone_name} to mount?",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SEC,
        ).pack(padx=24, pady=(0, 16), anchor="w")

        self._selected = ctk.StringVar(value="")

        for label, path in MOUNT_FOLDER_OPTIONS:
            btn = ctk.CTkRadioButton(
                self, text=label, variable=self._selected, value=path,
                font=ctk.CTkFont(size=14),
                text_color=COLOR_TEXT,
                radiobutton_width=18, radiobutton_height=18,
            )
            btn.pack(padx=32, pady=4, anchor="w")

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(16, 24))

        ctk.CTkButton(
            btn_frame, text="Cancel", width=100,
            fg_color="transparent", hover_color="#30363d",
            border_color=COLOR_CARD_BORDER, border_width=1,
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_frame, text="Mount", width=100,
            fg_color=COLOR_ACCENT, hover_color="#818cf8",
            command=self._submit,
        ).pack(side="right")

    def _submit(self):
        self.result = self._selected.get()
        self._on_select(self.result)
        self.destroy()


class CertWarningDialog(ctk.CTkToplevel):
    """Warning dialog when a server's certificate fingerprint changes."""

    def __init__(self, master, phone_name: str, old_fp: str, new_fp: str, on_accept, on_reject):
        super().__init__(master)
        self.title("Certificate Changed")
        self.geometry("500x300")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_BG)

        self.transient(master)
        self.grab_set()

        ctk.CTkLabel(
            self, text="⚠️  Server Certificate Changed",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=COLOR_ORANGE,
        ).pack(padx=24, pady=(24, 4), anchor="w")

        ctk.CTkLabel(
            self,
            text=(f"The TLS certificate for {phone_name} has changed.\n"
                  f"This could mean the phone was reset or the certificate\n"
                  f"was regenerated. It could also indicate a security issue."),
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SEC,
            justify="left",
            wraplength=450,
        ).pack(padx=24, pady=(4, 12), anchor="w")

        # Fingerprint comparison
        fp_frame = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=8)
        fp_frame.pack(fill="x", padx=24, pady=(0, 12))

        for label, fp in [("Previously saved:", old_fp), ("Current server:", new_fp)]:
            ctk.CTkLabel(fp_frame, text=label,
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=COLOR_TEXT_SEC).pack(padx=12, pady=(6, 0), anchor="w")
            # Show first 24 chars of fingerprint for readability
            display_fp = fp[:23] + "..." if len(fp) > 23 else fp
            ctk.CTkLabel(fp_frame, text=display_fp,
                         font=ctk.CTkFont(family="Consolas", size=10),
                         text_color=COLOR_TEXT).pack(padx=12, pady=(0, 6), anchor="w")

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(0, 24))

        ctk.CTkButton(
            btn_frame, text="Reject", width=100,
            fg_color=COLOR_RED, hover_color="#da3633",
            command=lambda: (on_reject(), self.destroy()),
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_frame, text="Accept & Continue", width=160,
            fg_color=COLOR_ACCENT, hover_color="#818cf8",
            command=lambda: (on_accept(), self.destroy()),
        ).pack(side="right")


class PhoneBridgeApp(ctk.CTk):
    """Main application window."""

    def __init__(self, scanner: PhoneScanner, mounter: MountManager, config: ConfigManager):
        super().__init__()

        self.scanner = scanner
        self.mounter = mounter
        self.config = config
        self._phone_statuses: dict = {}  # Cache for phone status API responses

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

        # Header accent line
        accent_line = ctk.CTkFrame(self, fg_color=COLOR_ACCENT, height=2, corner_radius=0)
        accent_line.pack(fill="x")

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

        # Connect Manually button
        ctk.CTkButton(
            header, text="🌐 Connect", width=100,
            fg_color="transparent", hover_color="#30363d",
            border_color=COLOR_CARD_BORDER, border_width=1,
            font=ctk.CTkFont(size=12),
            command=self._open_manual_connect,
        ).pack(side="right", padx=(0, 8))

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

        # ─── Footer Bar ───────────────────────────────────
        self._footer = ctk.CTkFrame(self, fg_color="#010409", height=28, corner_radius=0)
        self._footer.pack(fill="x", side="bottom")
        self._footer.pack_propagate(False)

        rclone_path = check_rclone()
        rclone_text = "rclone ✓" if rclone_path else "rclone ✗"
        from .tailscale import is_tailscale_installed
        ts_text = "Tailscale ✓" if is_tailscale_installed() else "Tailscale ✗"
        winfsp_text = "WinFsp ✓" if (check_winfsp() if sys.platform == "win32" else True) else "WinFsp ✗"

        ctk.CTkLabel(
            self._footer,
            text=f"v{__version__}  ·  {rclone_text}  ·  {winfsp_text}  ·  {ts_text}",
            font=ctk.CTkFont(size=10),
            text_color="#484f58",
        ).pack(side="left", padx=16)

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
        # Fetch phone statuses in background every 6 seconds for live stats
        self._poll_count = getattr(self, '_poll_count', 0) + 1
        if self._poll_count % 3 == 1:  # Every 6 seconds (3 * 2s)
            threading.Thread(target=self._fetch_phone_statuses, daemon=True).start()
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
            ctk.CTkLabel(empty, text="Not on the same network? Click \"🌐 Connect\" above to connect by IP",
                         font=ctk.CTkFont(size=12), text_color="#484f58").pack(pady=(8, 0))
            empty.pack(fill="both", expand=True)
            return

        # Create device cards
        for device_id, phone in phones.items():
            try:
                mount = mounts.get(device_id)
                phone_config = self.config.get_phone(device_id)
                phone_status = self._phone_statuses.get(device_id)
                card = DeviceCard(
                    self.device_scroll,
                    phone=phone,
                    mount_info=mount,
                    on_mount=self._handle_mount,
                    on_unmount=self._handle_unmount,
                    on_explorer=self._handle_explorer,
                    on_change_pass=self._handle_change_pass,
                    on_remove=self._handle_remove_phone,
                    on_toggle_automount=self._handle_toggle_automount,
                    phone_config=phone_config,
                    phone_status=phone_status,
                )
                card.pack(fill="x", pady=(0, 10))
            except Exception as e:
                logger.error(f"Failed to render card for {phone.display_name}: {e}")
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

    def _do_mount(self, phone, auth_user, password, mount_path=""):
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

            # Use saved mount_path from config if not explicitly provided
            if not mount_path and phone_config and phone_config.mount_path:
                mount_path = phone_config.mount_path

            # ─── TOFU Certificate Pinning ─────────────────────
            if phone.protocol == "https":
                current_fp = get_server_fingerprint(phone.ip_address, phone.port)
                saved_fp = phone_config.cert_fingerprint if phone_config else ""

                if current_fp:
                    if saved_fp and fingerprint_changed(saved_fp, current_fp):
                        # Fingerprint changed — ask user via a blocking event
                        accepted = threading.Event()
                        rejected = threading.Event()

                        self.after(0, lambda: CertWarningDialog(
                            self, phone.display_name, saved_fp, current_fp,
                            on_accept=accepted.set,
                            on_reject=rejected.set,
                        ))

                        # Wait for user decision (up to 60s)
                        while not accepted.is_set() and not rejected.is_set():
                            import time
                            time.sleep(0.1)

                        if rejected.is_set():
                            logger.warning(f"User rejected certificate change for {phone.display_name}")
                            self.after(0, lambda: self._show_error(
                                "Connection Rejected",
                                "The server certificate has changed and you chose to reject it."
                            ))
                            return

                        logger.info(f"User accepted new certificate for {phone.display_name}")

            mount_info = self.mounter.mount(
                phone, drive_letter,
                auth_user=auth_user,
                auth_password=password,
                mount_path=mount_path,
            )

            # Save config (with cert fingerprint and mount path)
            save_fp = ""
            if phone.protocol == "https":
                fp = get_server_fingerprint(phone.ip_address, phone.port)
                if fp:
                    save_fp = fp

            self.config.upsert_phone(PhoneConfig(
                device_id=phone.device_id,
                display_name=phone.display_name,
                last_ip=phone.ip_address,
                last_port=phone.port,
                preferred_drive=drive_letter,
                auth_user=auth_user,
                auth_password=password,
                mount_path=mount_path,
                cert_fingerprint=save_fp,
            ))

        except MountError as e:
            err_msg = str(e)
            self.after(0, lambda msg=err_msg: self._show_error("Mount Failed", msg))

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

    def _handle_remove_phone(self, device_id: str):
        """Remove a phone from saved config."""
        phone_config = self.config.get_phone(device_id)
        if phone_config:
            dialog = ctk.CTkToplevel(self)
            dialog.title("Forget Phone")
            dialog.geometry("380x150")
            dialog.configure(fg_color=COLOR_BG)
            dialog.transient(self)
            dialog.grab_set()

            ctk.CTkLabel(dialog, text=f"Forget {phone_config.display_name}?",
                         font=ctk.CTkFont(size=16, weight="bold"),
                         text_color=COLOR_TEXT).pack(padx=24, pady=(20, 4), anchor="w")
            ctk.CTkLabel(dialog, text="Saved password and preferences will be removed.",
                         font=ctk.CTkFont(size=13),
                         text_color=COLOR_TEXT_SEC).pack(padx=24, anchor="w")
            btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
            btn_frame.pack(fill="x", padx=24, pady=(16, 20))
            ctk.CTkButton(btn_frame, text="Cancel", width=80,
                          fg_color="transparent", border_color=COLOR_CARD_BORDER, border_width=1,
                          command=dialog.destroy).pack(side="right", padx=(8, 0))
            ctk.CTkButton(btn_frame, text="Forget", width=80,
                          fg_color=COLOR_RED, hover_color="#da3633",
                          command=lambda: (self.config.remove_phone(device_id), dialog.destroy())
                          ).pack(side="right")

    def _handle_toggle_automount(self, device_id: str, enabled: bool):
        """Toggle auto-mount for a phone."""
        phone_config = self.config.get_phone(device_id)
        if phone_config:
            phone_config.auto_mount = enabled
            self.config.upsert_phone(phone_config)
        else:
            # Create a minimal config entry
            phones = self.scanner.get_phones()
            phone = phones.get(device_id)
            if phone:
                self.config.upsert_phone(PhoneConfig(
                    device_id=device_id,
                    display_name=phone.display_name,
                    auto_mount=enabled,
                ))

    def _handle_change_pass(self, phone: DiscoveredPhone):
        self._show_password_dialog(phone)

    # ─── Settings ─────────────────────────────────────────────

    def _fetch_phone_statuses(self):
        """Fetch storage/status info from all discovered phones via REST API."""
        phones = self.scanner.get_phones()
        for device_id, phone in phones.items():
            try:
                url = f"{phone.webdav_url}/phonebridge/status"
                req = urllib.request.Request(url, method="GET")

                # Add auth if available
                phone_config = self.config.get_phone(device_id)
                if phone_config and phone_config.auth_password:
                    import base64
                    user = phone_config.auth_user or "phonebridge"
                    creds = base64.b64encode(f"{user}:{phone_config.auth_password}".encode()).decode()
                    req.add_header("Authorization", f"Basic {creds}")

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
                    data = json.loads(resp.read().decode())
                    self._phone_statuses[device_id] = data
            except Exception as e:
                logger.debug(f"Failed to fetch status for {device_id}: {e}")

    def _open_settings(self):
        settings_win = ctk.CTkToplevel(self)
        settings_win.title("PhoneBridge Settings")
        settings_win.geometry("440x620")
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

        # Remote Access
        ctk.CTkLabel(settings_win, text="Remote Access",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLOR_TEXT_SEC).pack(padx=24, pady=(16, 8), anchor="w")

        remote_frame = ctk.CTkFrame(settings_win, fg_color=COLOR_CARD, corner_radius=10)
        remote_frame.pack(fill="x", padx=24)

        # Check Tailscale status
        from .tailscale import is_tailscale_installed
        ts_installed = is_tailscale_installed()

        ts_row = ctk.CTkFrame(remote_frame, fg_color="transparent")
        ts_row.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(ts_row,
                     text=f"{'✅' if ts_installed else '○'}  VPN Tunnel (Tailscale)",
                     font=ctk.CTkFont(size=13),
                     text_color=COLOR_GREEN if ts_installed else COLOR_TEXT_SEC,
        ).pack(side="left")
        ctk.CTkLabel(ts_row,
                     text="Ready" if ts_installed else "Not set up",
                     font=ctk.CTkFont(size=12),
                     text_color=COLOR_GREEN if ts_installed else COLOR_TEXT_SEC,
        ).pack(side="right")

        if not ts_installed:
            guide_text = (
                "To access your phone from any network:\n"
                "1. Download Tailscale (free) on this PC and your phone\n"
                "2. Sign in with the same Google account on both\n"
                "3. PhoneBridge will auto-discover your phone remotely"
            )
            ctk.CTkLabel(remote_frame, text=guide_text,
                         font=ctk.CTkFont(size=11),
                         text_color=COLOR_TEXT_SEC,
                         justify="left",
                         wraplength=380,
            ).pack(padx=16, pady=(4, 4), anchor="w")

            ctk.CTkButton(
                remote_frame, text="Set Up Remote Access", width=200,
                fg_color=COLOR_ACCENT, hover_color="#818cf8",
                font=ctk.CTkFont(size=12),
                command=lambda: webbrowser.open("https://tailscale.com/download"),
            ).pack(padx=16, pady=(4, 12))
        else:
            ctk.CTkLabel(remote_frame,
                         text="Remote phones are auto-discovered on your VPN network.",
                         font=ctk.CTkFont(size=11),
                         text_color=COLOR_TEXT_SEC,
            ).pack(padx=16, pady=(4, 12), anchor="w")

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

        # About section
        ctk.CTkLabel(settings_win, text="About",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLOR_TEXT_SEC).pack(padx=24, pady=(16, 8), anchor="w")

        about_frame = ctk.CTkFrame(settings_win, fg_color=COLOR_CARD, corner_radius=10)
        about_frame.pack(fill="x", padx=24, pady=(0, 16))

        ctk.CTkLabel(about_frame, text=f"PhoneBridge v{__version__}",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLOR_TEXT).pack(padx=16, pady=(12, 2), anchor="w")
        ctk.CTkLabel(about_frame, text="Mount phone storage as Windows drive letters — wirelessly.",
                     font=ctk.CTkFont(size=12),
                     text_color=COLOR_TEXT_SEC).pack(padx=16, anchor="w")
        ctk.CTkLabel(about_frame, text="License: GNU GPL v3.0",
                     font=ctk.CTkFont(size=11),
                     text_color=COLOR_TEXT_SEC).pack(padx=16, anchor="w")

        link_frame = ctk.CTkFrame(about_frame, fg_color="transparent")
        link_frame.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkButton(
            link_frame, text="GitHub →", width=80,
            fg_color="transparent", hover_color="#30363d",
            text_color=COLOR_ACCENT, font=ctk.CTkFont(size=12),
            command=lambda: webbrowser.open("https://github.com/ysachin26/PhoneBridge"),
        ).pack(side="left")

    def _set_notifications(self, enabled):
        self.config.config.show_notifications = enabled
        self.config.save()

    def _set_cache_mode(self, mode):
        self.config.config.vfs_cache_mode = mode
        self.config.save()

    def _rescan(self):
        self.scanner.stop()
        self.scanner.start()

    # ─── Manual Connect ────────────────────────────────────────

    def _open_manual_connect(self):
        """Open the manual connection dialog."""
        ManualConnectDialog(self, on_connect=self._handle_manual_connect)

    def _handle_manual_connect(self, address, port, protocol, password, dialog):
        """Validate and connect to a manually specified phone."""
        def do_connect():
            try:
                phone = DiscoveredPhone.create_manual(
                    ip_address=address,
                    port=port,
                    protocol=protocol,
                )

                # Verify connection by calling the status endpoint
                url = f"{phone.webdav_url}/phonebridge/status"
                req = urllib.request.Request(url, method="GET")
                import base64
                creds = base64.b64encode(f"phonebridge:{password}".encode()).decode()
                req.add_header("Authorization", f"Basic {creds}")

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    data = json.loads(resp.read().decode())

                # Update display name from status if available
                device_name = data.get("device_name", f"Phone ({address})")
                if device_name and device_name != f"Phone ({address})":
                    phone.display_name = device_name

                # Add to scanner's phone list so it appears in the UI
                with self.scanner._lock:
                    self.scanner._phones[phone.device_id] = phone

                # Close dialog and mount
                self.after(0, dialog.destroy)
                self._do_mount(phone, "phonebridge", password)

                # Save config as manual connection
                self.config.upsert_phone(PhoneConfig(
                    device_id=phone.device_id,
                    display_name=phone.display_name,
                    last_ip=address,
                    last_port=port,
                    auth_user="phonebridge",
                    auth_password=password,
                    connection_type="manual",
                    protocol=protocol,
                ))

            except urllib.error.HTTPError as e:
                if e.code == 401:
                    self.after(0, lambda: dialog.show_error("Incorrect connection code"))
                else:
                    code = e.code
                    self.after(0, lambda c=code: dialog.show_error(f"Server error: HTTP {c}"))
            except urllib.error.URLError:
                self.after(0, lambda: dialog.show_error(
                    f"Could not reach {protocol}://{address}:{port}\nCheck the address and make sure PhoneBridge is running."))
            except Exception as e:
                err_msg = str(e)
                self.after(0, lambda msg=err_msg: dialog.show_error(f"Connection failed: {msg}"))

        threading.Thread(target=do_connect, daemon=True).start()

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
