 

<h1 align="center">PhoneBridge</h1>
<p align="center">
  <strong>Mount your phone storage as a real drive letter — wirelessly, with one click.</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#installation">Installation</a> •
  <a href="#usage">Usage</a> •
  <a href="#building-from-source">Build</a> •
  <a href="#contributing">Contributing</a> •
  <a href="#license">License</a>
</p>

---

## Features

- 📱 **One-click mount** — Phone storage appears as E:, F:, G: drives in Windows Explorer
- 📡 **Wireless** — No USB cable needed, works over Wi-Fi
- 🔍 **Auto-discovery** — Phones are detected automatically via mDNS
- 📱📱📱 **Multi-phone** — Mount multiple phones simultaneously as separate drive letters
- 🔓 **Open source** — Free forever, GPL v3 licensed
- 🔒 **Private** — Direct phone-to-PC connection, no cloud, no middleman

## How It Works

```
┌──────────────┐         Wi-Fi / LAN          ┌──────────────┐
│  Android App │ ◄──── WebDAV over HTTP ────► │  PC Tray App │
│  (Server)    │ ◄──── mDNS Discovery   ────► │  (Client)    │
│              │                               │              │
│  NanoHTTPD   │                               │  rclone      │
│  WebDAV :8273│                               │  → E: drive  │
└──────────────┘                               └──────────────┘
```

1. **Android app** runs a lightweight WebDAV file server in the background
2. **PC tray app** discovers phones on the network via mDNS
3. Click "Mount" → rclone maps the phone's WebDAV to a real drive letter
4. Browse, copy, open files in Windows Explorer — just like C: or D:

## Installation

### PC App (Windows)
1. Download the latest release from [Releases](https://github.com/ysachin26/PhoneBridge/releases)
2. Install [WinFsp](https://winfsp.dev/rel/) (required for drive mounting)
3. Install [rclone](https://rclone.org/downloads/) and add it to your PATH
4. Run `PhoneBridge.exe`

### Android App
1. Download the APK from [Releases](https://github.com/ysachin26/PhoneBridge/releases)
2. Install and grant storage permissions
3. Tap "Start Server"

## Usage

1. Open PhoneBridge on your Android phone → tap **Start**
2. On your PC, PhoneBridge tray icon will show the phone automatically
3. Right-click tray icon → click your phone name → **Mount**
4. Open Windows Explorer → your phone appears as a new drive letter
5. Done! Browse files, drag-and-drop, open directly from apps

## Building from Source

### PC App
```bash
cd desktop
pip install -r requirements.txt
python -m phonebridge.main
```

### Android App
```bash
cd android
./gradlew assembleDebug
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Android Server | Kotlin + NanoHTTPD (WebDAV) |
| PC Client | Python + pystray + rclone |
| Discovery | mDNS/DNS-SD (NsdManager + zeroconf) |
| Mounting | rclone + WinFsp |
| Protocol | WebDAV over HTTP |

## Roadmap

- [x] Phase 1: LAN mounting (same Wi-Fi)
- [ ] Phase 2: Remote access via Tailscale (phone anywhere)
- [ ] Phase 3: Unified file browser across all phones
- [ ] Phase 4: Linux & macOS PC support

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the GNU General Public License v3.0 — see the [LICENSE](LICENSE) file for details.
