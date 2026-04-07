"""
PhoneBridge Desktop — Main Entry Point

Starts the native GUI window + system tray with mDNS discovery and rclone mounting.
"""

import sys
import os
import argparse
import logging
import ctypes
import ctypes.wintypes
import threading

from . import __version__, __app_name__
from .utils import setup_logging, check_rclone, check_winfsp
from .config import ConfigManager
from .discovery import PhoneScanner
from .mounter import MountManager
from .tray import TrayIcon
from .gui import PhoneBridgeApp


# ─── Single Instance Lock ────────────────────────────────────────────

_MUTEX_NAME = "PhoneBridge_SingleInstance_Mutex"
_mutex_handle = None


def _acquire_single_instance() -> bool:
    global _mutex_handle
    if sys.platform != "win32":
        return True
    try:
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        last_error = ctypes.windll.kernel32.GetLastError()
        if last_error == 183:
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
            _mutex_handle = None
            return False
        return True
    except Exception:
        return True


def _release_single_instance():
    global _mutex_handle
    if _mutex_handle:
        try:
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        except Exception:
            pass
        _mutex_handle = None


def _show_already_running_message():
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                "PhoneBridge is already running in the system tray.\n\n"
                "Look for the 📱 icon in your taskbar notification area.",
                "PhoneBridge",
                0x40,
            )
        except Exception:
            pass


def print_banner():
    if getattr(sys, "frozen", False):
        return
    try:
        print("""
    +-------------------------------------------+
    |                                           |
    |   PhoneBridge  v{:<25s}|
    |                                           |
    |   Mount phone storage as drive letters    |
    |   wirelessly, with one click.             |
    |                                           |
    +-------------------------------------------+
    """.format(__version__))
    except Exception:
        pass


def check_system(logger):
    all_ok = True
    rclone = check_rclone()
    if rclone:
        logger.info(f"✅ rclone found: {rclone}")
    else:
        logger.warning("❌ rclone not found!")
        all_ok = False

    if sys.platform == "win32":
        if check_winfsp():
            logger.info("✅ WinFsp found")
        else:
            logger.warning("❌ WinFsp not found!")
            all_ok = False

    return all_ok


# ─── Main ────────────────────────────────────────────────────────────

def main():
    """Main entry point."""
    if not _acquire_single_instance():
        _show_already_running_message()
        sys.exit(0)

    parser = argparse.ArgumentParser(
        prog="phonebridge",
        description="PhoneBridge — Mount phone storage as Windows drive letters",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--version", action="version", version=f"PhoneBridge {__version__}")
    parser.add_argument("--no-tray", action="store_true", help="CLI mode for testing")
    parser.add_argument("--no-gui", action="store_true", help="Tray only, no window")

    args = parser.parse_args()

    print_banner()
    logger = setup_logging(debug=args.debug)
    logger.info(f"PhoneBridge v{__version__} starting...")

    deps_ok = check_system(logger)
    if not deps_ok:
        logger.warning("Some dependencies are missing — mounting will not work")

    # Shared components
    config = ConfigManager()
    scanner = PhoneScanner()
    mounter = MountManager(
        vfs_cache_mode=config.config.vfs_cache_mode,
        vfs_cache_max_age=config.config.vfs_cache_max_age,
        vfs_read_chunk_size=config.config.vfs_read_chunk_size,
    )

    if args.no_tray:
        # CLI mode
        logger.info("Running in CLI mode...")
        scanner._on_found = lambda phone: logger.info(f"📱 Found: {phone}")
        scanner._on_lost = lambda did: logger.info(f"📱 Lost: {did}")
        scanner.start()
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            scanner.stop()
    elif args.no_gui:
        # Tray-only
        tray = TrayIcon(scanner, mounter, config)
        try:
            tray.start()
        except KeyboardInterrupt:
            pass
        finally:
            tray.stop()
            _release_single_instance()
    else:
        # Full mode: GUI + tray
        app = PhoneBridgeApp(scanner, mounter, config)

        # Start tray in background (with reference to GUI for "Open" action)
        tray = TrayIcon(scanner, mounter, config, gui=app)
        tray_thread = threading.Thread(target=tray.start, daemon=True)
        tray_thread.start()

        # Start scanner and health monitor
        scanner.start()
        mounter.start_health_monitor()

        try:
            # Run GUI on main thread (tkinter requirement)
            app.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            tray.stop()
            mounter.unmount_all()
            mounter.stop_health_monitor()
            scanner.stop()
            _release_single_instance()
            logger.info("PhoneBridge stopped.")


if __name__ == "__main__":
    main()
