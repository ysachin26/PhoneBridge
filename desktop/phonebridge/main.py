"""
PhoneBridge Desktop — Main Entry Point

Starts the system tray application with mDNS discovery and rclone mounting.
"""

import sys
import argparse
import logging

from . import __version__, __app_name__
from .utils import setup_logging, check_rclone, check_winfsp
from .config import ConfigManager
from .discovery import PhoneScanner
from .mounter import MountManager
from .tray import TrayIcon


def print_banner():
    """Print startup banner."""
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


def main():
    """Main entry point."""
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
            logger.info("PhoneBridge stopped. Goodbye!")


if __name__ == "__main__":
    main()
