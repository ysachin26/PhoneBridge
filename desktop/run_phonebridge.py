"""
PhoneBridge Desktop — Entry Point for PyInstaller

This is the top-level script that PyInstaller uses as the entry point.
It must be a standalone script (not a package __main__).
"""

import multiprocessing
import sys
import os

def main():
    # Required for PyInstaller on Windows (fixes multiprocessing in frozen exes)
    multiprocessing.freeze_support()

    # Add the parent directory to sys.path so imports work
    if getattr(sys, "frozen", False):
        # Running as compiled exe — set up paths
        base_dir = sys._MEIPASS  # PyInstaller temp extraction folder
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    # Import and run
    from phonebridge.main import main as app_main
    app_main()

if __name__ == "__main__":
    main()
