"""
PhoneBridge — Build Script

Packages the desktop app as a standalone .exe using PyInstaller.
Run: python build.py
Output: dist/PhoneBridge.exe
"""

import subprocess
import sys
import os

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # Install PyInstaller if needed
    try:
        import PyInstaller
        print(f"PyInstaller {PyInstaller.__version__} found")
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Build command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",
        "--name=PhoneBridge",
        "--clean",
        # Hidden imports
        "--hidden-import=zeroconf",
        "--hidden-import=zeroconf._utils",
        "--hidden-import=zeroconf._handlers",
        "--hidden-import=zeroconf._protocol",
        "--hidden-import=zeroconf._listener",
        "--hidden-import=zeroconf._engine",
        "--hidden-import=zeroconf._updates",
        "--hidden-import=zeroconf._dns",
        "--hidden-import=pystray",
        "--hidden-import=pystray._win32",
        "--hidden-import=PIL",
        "--hidden-import=PIL.Image",
        "--hidden-import=PIL.ImageDraw",
        "--hidden-import=PIL.ImageFont",
        "--hidden-import=customtkinter",
        "--hidden-import=darkdetect",
        # Collect all customtkinter assets (themes, etc)
        "--collect-all=customtkinter",
        "--collect-all=zeroconf",
        # Entry point
        "run_phonebridge.py",
    ]

    print("\nBuilding PhoneBridge.exe...")
    print(f"  Command: {' '.join(cmd)}\n")

    result = subprocess.run(cmd)

    if result.returncode == 0:
        exe_path = os.path.join(script_dir, "dist", "PhoneBridge.exe")
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print(f"\nBuild successful!")
            print(f"  Output: {exe_path}")
            print(f"  Size:   {size_mb:.1f} MB")
        else:
            print("\nBuild completed but exe not found")
    else:
        print(f"\nBuild failed with exit code {result.returncode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
