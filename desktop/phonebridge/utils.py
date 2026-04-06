"""
PhoneBridge — Utilities & Logging
"""

import logging
import os
import sys
import shutil
import subprocess
from pathlib import Path


def get_app_data_dir() -> Path:
    """Get the PhoneBridge app data directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))

    app_dir = base / "PhoneBridge"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def setup_logging(debug: bool = False) -> logging.Logger:
    """Configure logging for PhoneBridge."""
    log_dir = get_app_data_dir() / "logs"
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / "phonebridge.log"

    level = logging.DEBUG if debug else logging.INFO

    # File handler with rotation
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_formatter)

    # Root logger
    root = logging.getLogger("phonebridge")
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    return root


def check_rclone() -> str | None:
    """Check if rclone is available. Returns path if found, None otherwise."""
    rclone_path = shutil.which("rclone")
    if rclone_path:
        return rclone_path

    # Check common install locations on Windows
    if sys.platform == "win32":
        common_paths = [
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "rclone" / "rclone.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "rclone" / "rclone.exe",
            Path.home() / "rclone" / "rclone.exe",
            Path.home() / "scoop" / "apps" / "rclone" / "current" / "rclone.exe",
        ]

        # Scan for versioned folders (e.g., C:\rclone-v1.73.3-windows-amd64\rclone.exe)
        search_roots = [Path("C:\\"), Path.home() / "Downloads"]
        for root in search_roots:
            if root.exists():
                try:
                    for entry in root.iterdir():
                        if entry.is_dir() and entry.name.lower().startswith("rclone"):
                            potential_path = entry / "rclone.exe"
                            if potential_path.exists():
                                return str(potential_path)
                except (PermissionError, OSError):
                    continue

        for p in common_paths:
            if p.exists():
                return str(p)

    return None


def check_winfsp() -> bool:
    """Check if WinFsp is installed (required for rclone mount on Windows)."""
    if sys.platform != "win32":
        return True  # Not needed on other platforms

    winfsp_paths = [
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "WinFsp",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")) / "WinFsp",
    ]
    return any(p.exists() for p in winfsp_paths)


def get_available_drive_letters() -> list[str]:
    """Get list of available (unused) drive letters on Windows."""
    if sys.platform != "win32":
        return []

    import string
    used = set()
    # Use subprocess to get used drive letters
    try:
        result = subprocess.run(
            ["wmic", "logicaldisk", "get", "caption"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if len(line) >= 2 and line[1] == ":":
                used.add(line[0].upper())
    except Exception:
        # Fallback: assume C and D are used
        used = {"C", "D"}

    # Return available letters from E onwards (skip A, B which are floppy)
    all_letters = list(string.ascii_uppercase)
    available = [f"{letter}:" for letter in all_letters if letter not in used and letter not in ("A", "B")]
    return available


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"
