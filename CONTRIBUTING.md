# Contributing to PhoneBridge

Thank you for your interest in contributing! Here's how to get started.

## Development Setup

### Android App

**Prerequisites:**
- Android Studio (Arctic Fox or later)
- JDK 17+
- Android device or emulator (API 26+)

**Steps:**
```bash
cd android
./gradlew assembleDebug
```

Install the debug APK on your device:
```bash
adb install app/build/outputs/apk/debug/app-debug.apk
```

### Desktop App (Windows)

**Prerequisites:**
- Python 3.10+
- [rclone](https://rclone.org/downloads/) installed and in PATH
- [WinFsp](https://winfsp.dev/rel/) installed

**Steps:**
```bash
cd desktop
pip install -r requirements.txt
python -m phonebridge.main
```

**Run in different modes:**
```bash
# Full mode (GUI + tray)
python -m phonebridge.main

# Tray-only mode
python -m phonebridge.main --no-gui

# CLI mode (for testing)
python -m phonebridge.main --no-tray

# Debug logging
python -m phonebridge.main --debug
```

### Building the Desktop Executable

```bash
cd desktop
python build.py
```

The output will be at `desktop/dist/PhoneBridge.exe`.

## Project Structure

```
├── android/                    # Android app (Kotlin)
│   └── app/src/main/java/com/phonebridge/
│       ├── MainActivity.kt     # Main UI activity
│       ├── service/            # Foreground service (WebDAV + mDNS)
│       ├── server/             # WebDAV server (NanoHTTPD)
│       ├── discovery/          # mDNS advertising (NSD)
│       └── receiver/           # Boot receiver for auto-start
│
├── desktop/                    # Desktop app (Python)
│   ├── phonebridge/
│   │   ├── main.py             # Entry point
│   │   ├── gui.py              # customtkinter GUI window
│   │   ├── tray.py             # System tray icon (pystray)
│   │   ├── discovery.py        # mDNS scanner (zeroconf)
│   │   ├── mounter.py          # rclone mount manager
│   │   ├── config.py           # JSON config persistence
│   │   ├── startup.py          # Windows auto-start (registry)
│   │   └── utils.py            # Logging, dependency checks
│   ├── build.py                # PyInstaller build script
│   └── installer.iss           # Inno Setup installer script
```

## Code Style

### Kotlin (Android)
- Follow standard Kotlin conventions
- Use `Log.d/i/w/e(TAG, message)` for logging
- Keep services lean — delegate to helper classes

### Python (Desktop)
- Follow PEP 8
- Use type hints for function signatures
- Use `logging.getLogger("phonebridge.module")` for logging
- Use threading for background operations, never block the UI thread

## Making Changes

1. **Fork** the repository
2. **Create a branch** for your feature: `git checkout -b feature/my-feature`
3. **Make your changes** and test locally
4. **Commit** with clear messages: `git commit -m "Add storage info to tray menu"`
5. **Push** and open a **Pull Request**

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Test on a real Android device + Windows PC before submitting
- Update the README if you add user-facing features
- Add comments for non-obvious logic

## Reporting Issues

When filing an issue, please include:
- **OS version** (Windows 10/11, Android version)
- **Steps to reproduce**
- **Expected vs actual behavior**
- **Logs** from `%APPDATA%\PhoneBridge\logs\phonebridge.log` (desktop)

## License

By contributing, you agree that your contributions will be licensed under the [GNU GPL v3.0](LICENSE).
