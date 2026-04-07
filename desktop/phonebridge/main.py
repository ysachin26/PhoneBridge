"""
PhoneBridge Desktop — Main Entry Point

Starts the system tray application with mDNS discovery and rclone mounting.
"""

import sys
import os
import argparse
import logging
import ctypes
import ctypes.wintypes

from . import __version__, __app_name__
from .utils import setup_logging, check_rclone, check_winfsp
from .config import ConfigManager
from .discovery import PhoneScanner
from .mounter import MountManager
from .tray import TrayIcon


# ─── Single Instance Lock ────────────────────────────────────────────

_MUTEX_NAME = "PhoneBridge_SingleInstance_Mutex"
_mutex_handle = None


def _acquire_single_instance() -> bool:
    """
    Ensure only one instance of PhoneBridge runs at a time.
    Uses a Windows named mutex.
    Returns True if this is the first instance, False if another is already running.
    """
    global _mutex_handle
    if sys.platform != "win32":
        return True

    try:
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        last_error = ctypes.windll.kernel32.GetLastError()
        # ERROR_ALREADY_EXISTS = 183
        if last_error == 183:
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
            _mutex_handle = None
            return False
        return True
    except Exception:
        return True


def _release_single_instance():
    """Release the single-instance mutex."""
    global _mutex_handle
    if _mutex_handle:
        try:
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        except Exception:
            pass
        _mutex_handle = None


# ─── Banner ──────────────────────────────────────────────────────────

def print_banner():
    """Print startup banner (only in terminal mode)."""
    if getattr(sys, "frozen", False):
        return  # Don't print banner in compiled exe (no console)
    print(r"""
    ╔═══════════════════════════════════════════╗
    ║                                           ║
    ║   📱  PhoneBridge  v{:<21s} ║
    ║                                           ║
    ║   Mount phone storage as drive letters    ║
    ║   wirelessly, with one click.             ║
    ║                                           ║
    ╚═══════════════════════════════════════════╝
    """.format(__version__))


# ─── System Checks ───────────────────────────────────────────────────

def check_system(logger: logging.Logger) -> bool:
    """Pre-flight system checks."""
    all_ok = True

    # Check rclone
    rclone = check_rclone()
    if rclone:
        logger.info(f"✅ rclone found: {rclone}")
    else:
        logger.warning("❌ rclone not found!")
        logger.warning("   Download from: https://rclone.org/downloads/")
        logger.warning("   Add to PATH or install to a standard location")
        all_ok = False

    # Check WinFsp (Windows only)
    if sys.platform == "win32":
        if check_winfsp():
            logger.info("✅ WinFsp found")
        else:
            logger.warning("❌ WinFsp not found!")
            logger.warning("   Download from: https://winfsp.dev/rel/")
            logger.warning("   Required for mounting drives on Windows")
            all_ok = False

    return all_ok


def _show_already_running_message():
    """Show a Windows message box telling the user PhoneBridge is already running."""
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                "PhoneBridge is already running in the system tray.\n\n"
                "Look for the 📱 icon in your taskbar notification area.",
                "PhoneBridge",
                0x40,  # MB_ICONINFORMATION
            )
        except Exception:
            pass


# ─── Main ────────────────────────────────────────────────────────────

def main():
    """Main entry point."""
    # Single instance check
    if not _acquire_single_instance():
        _show_already_running_message()
        sys.exit(0)

    parser = argparse.ArgumentParser(
        prog="phonebridge",
        description="PhoneBridge — Mount phone storage as Windows drive letters",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"PhoneBridge {__version__}",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Run without system tray (CLI mode for testing)",
    )

    args = parser.parse_args()

    # Setup
    print_banner()
    logger = setup_logging(debug=args.debug)
    logger.info(f"PhoneBridge v{__version__} starting...")

    # System checks
    deps_ok = check_system(logger)
    if not deps_ok:
        logger.warning("Some dependencies are missing — mounting will not work")
        logger.warning("PhoneBridge will still run for discovery and testing")

    # Initialize components
    config = ConfigManager()
    scanner = PhoneScanner()
    mounter = MountManager(
        vfs_cache_mode=config.config.vfs_cache_mode,
        vfs_cache_max_age=config.config.vfs_cache_max_age,
        vfs_read_chunk_size=config.config.vfs_read_chunk_size,
    )

    if args.no_tray:
        # CLI mode for testing
        logger.info("Running in CLI mode (no tray)...")
        logger.info("Scanning for phones on the network...")
        scanner._on_found = lambda phone: logger.info(f"📱 Found: {phone}")
        scanner._on_lost = lambda did: logger.info(f"📱 Lost: {did}")
        scanner.start()

        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Interrupted — shutting down...")
            scanner.stop()
    else:
        # System tray mode
        tray = TrayIcon(scanner, mounter, config)
        try:
            tray.start()  # Blocking
        except KeyboardInterrupt:
            pass
        finally:
            tray.stop()
            _release_single_instance()
            logger.info("PhoneBridge stopped. Goodbye!")


if __name__ == "__main__":
    main()
