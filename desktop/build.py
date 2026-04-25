"""
PhoneBridge — Build Script

Packages the desktop app as a standalone .exe using PyInstaller.

Usage:
    python build.py          # Build normally
    python build.py --skip-tests  # Skip tests before building

Output: dist/PhoneBridge.exe
"""

import subprocess
import sys
import os
import importlib


def get_version() -> str:
    """Read the version from phonebridge/__init__.py."""
    spec = importlib.util.spec_from_file_location(
        "phonebridge",
        os.path.join(os.path.dirname(__file__), "phonebridge", "__init__.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.__version__


def run_tests() -> bool:
    """Run the test suite before building. Returns True if all tests pass."""
    print("\n🧪 Running tests before build...\n")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    if result.returncode != 0:
        print("\n❌ Tests failed — aborting build.")
        return False
    print("\n✅ All tests passed.\n")
    return True


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    skip_tests = "--skip-tests" in sys.argv

    version = get_version()
    print(f"\n📦 PhoneBridge Build System")
    print(f"   Version:  {version}")
    print(f"   Python:   {sys.version.split()[0]}")
    print(f"   Platform: {sys.platform}")

    # Run tests first (unless skipped)
    if not skip_tests:
        if not run_tests():
            sys.exit(1)

    # Install PyInstaller if needed
    try:
        import PyInstaller
        print(f"   PyInstaller: {PyInstaller.__version__}")
    except ImportError:
        print("   Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Use .ico if available, fall back to .png
    icon_path = "assets/icon.ico" if os.path.exists("assets/icon.ico") else "assets/icon.png"

    # Build command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",
        f"--name=PhoneBridge",
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
        # Icon
        f"--icon={icon_path}",
        # Bundle assets
        "--add-data=assets;assets",
        # Entry point
        "run_phonebridge.py",
    ]

    print(f"\n🔨 Building PhoneBridge.exe...\n")

    result = subprocess.run(cmd)

    if result.returncode == 0:
        exe_path = os.path.join(script_dir, "dist", "PhoneBridge.exe")
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print(f"\n{'='*50}")
            print(f"  ✅ Build successful!")
            print(f"  📁 Output:  {exe_path}")
            print(f"  📊 Size:    {size_mb:.1f} MB")
            print(f"  🏷  Version: {version}")
            print(f"{'='*50}\n")
        else:
            print("\n⚠  Build completed but exe not found")
    else:
        print(f"\n❌ Build failed with exit code {result.returncode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
