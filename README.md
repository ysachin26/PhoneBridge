<h1 align="center">PhoneBridge</h1>
<p align="center">
  <strong>Mount your phone storage as a real drive letter — wirelessly, with one click.</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#installation">Installation</a> •
  <a href="#usage">Usage</a> •
  <a href="#security">Security</a> •
  <a href="#building-from-source">Build</a> •
  <a href="#troubleshooting">Troubleshooting</a> •
  <a href="#contributing">Contributing</a> •
  <a href="#license">License</a>
</p>

---

## Features

- **One-click mount** — Phone storage appears as E:, F:, G: drives in Windows Explorer
- **Wireless** — No USB cable needed, works over Wi-Fi
- **Remote access** — Connect from any network with built-in VPN tunnel setup
- **Auto-discovery** — Phones are detected automatically via mDNS + Tailscale
- **Multi-phone** — Mount multiple phones simultaneously as separate drive letters
- **Open source** — Free forever, GPL v3 licensed
- **Private** — Direct phone-to-PC connection, no cloud, no middleman

## How It Works

```
┌──────────────────┐       Wi-Fi / LAN        ┌───────────────────┐
│   Android App    │ ◄── WebDAV over HTTPS ──► │   PC Client App   │
│   (Server)       │ ◄── mDNS Discovery   ──► │   (GUI + Tray)    │
│                  │                           │                   │
│   NanoHTTPD      │                           │   rclone mount    │
│   WebDAV :8273   │                           │   → E: drive      │
│   TLS + Auth     │                           │   customtkinter   │
└──────────────────┘                           └───────────────────┘
```

1. **Android app** runs a WebDAV file server with HTTPS encryption and password protection
2. **PC client** discovers phones on the network automatically via mDNS
3. Enter the connection code shown on your phone → rclone maps it to a real drive letter
4. Browse, copy, and open files in Windows Explorer — just like C: or D:

## Installation

### PC App (Windows)

**Option A: Installer (recommended)**
1. Download `PhoneBridge_Setup.exe` from [Releases](https://github.com/ysachin26/PhoneBridge/releases)
2. Run the installer — it will set up everything automatically
3. Install [WinFsp](https://winfsp.dev/rel/) if not already installed
4. Install [rclone](https://rclone.org/downloads/) and add it to your PATH

**Option B: Portable**
1. Download `PhoneBridge.exe` from [Releases](https://github.com/ysachin26/PhoneBridge/releases)
2. Install [WinFsp](https://winfsp.dev/rel/) and [rclone](https://rclone.org/downloads/)
3. Run `PhoneBridge.exe`

### Android App
1. Download the APK from [Releases](https://github.com/ysachin26/PhoneBridge/releases)
2. Install and grant storage + notification permissions
3. Tap the power button to start sharing

## Usage

1. **Phone**: Open PhoneBridge → tap the **power button** to start the server
2. **PC**: PhoneBridge discovers your phone automatically (system tray + GUI window)
3. **Connect**: Click **Mount Drive** → enter the connection code shown on your phone
4. **Browse**: Open Windows Explorer → your phone appears as a new drive letter (e.g., E:)
5. **Done!** Browse files, drag-and-drop, open directly from any app

### Android UI
- **Toggle button** with pulse animation when server is active
- **Live stats dashboard** — upload/download speeds, uptime, active connections
- **Storage bar** — visual indicator of phone storage usage
- **Folder selection** — share All Storage, DCIM, Downloads, or Music
- **Password management** — copy or regenerate the connection code
- **Auto-start toggle** — automatically start when phone boots

### Desktop UI
- **Device cards** — see all discovered phones with status, storage info, and actions
- **System tray** — mount/unmount from the tray icon, even without the main window
- **Settings** — start with Windows, notification preferences, VFS cache mode
- **Auto-mount** — automatically mount saved phones when detected

## Remote Access

PhoneBridge includes built-in support for remote access — connect to your phone from **any network** (different Wi-Fi, mobile data, office, etc.).

### One-Time Setup (~2 minutes)

1. **On your phone**: Open PhoneBridge → scroll to "Remote Access" → tap **Set Up Remote Access**
2. **Follow the prompts**: Install the free VPN tunnel and sign in with Google
3. **On your PC**: Open PhoneBridge Settings → click **Set Up Remote Access** (same Google account)
4. **Done!** Your phone now appears automatically — even from different networks

### Manual Connection

If you prefer not to use auto-discovery, you can connect by IP address directly:

1. **On your phone**: Note the remote address shown in the Remote Access card (e.g., `100.64.0.2:8273`)
2. **On your PC**: Click **🌐 Connect** → enter the address and connection code

> **How it works:** PhoneBridge uses [Tailscale](https://tailscale.com) (free, open-source) to create a private VPN tunnel between your devices. No port forwarding, no cloud servers, no data limits. Your files travel directly between phone and PC.

## Security

PhoneBridge uses multiple layers of security for all connections:

| Layer | Implementation |
|-------|---------------|
| **Transport Encryption** | HTTPS with self-signed TLS certificate (Bouncy Castle, 2048-bit RSA) |
| **Authentication** | HTTP Basic Auth with auto-generated 8-character password |
| **Password Storage** | SharedPreferences (Android), config.json (Desktop) |
| **Certificate Persistence** | PKCS12 keystore in app private storage — stable fingerprint |
| **mDNS Advertisement** | `auth_required=true` TXT record — desktop knows to prompt for password |
| **Password Rotation** | On-demand regeneration from the Android UI |

> **Note:** Since the certificate is self-signed, the desktop client uses `--no-check-certificate` with rclone. This is standard for LAN-only self-signed setups.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Android Server | Kotlin + NanoHTTPD (WebDAV) |
| Android TLS | Bouncy Castle (bcprov/bcpkix-jdk18on) |
| PC Client GUI | Python + customtkinter |
| PC System Tray | Python + pystray |
| Discovery | mDNS/DNS-SD (NsdManager + zeroconf) |
| Mounting | rclone + WinFsp |
| Protocol | WebDAV over HTTPS with Basic Auth |
| Build (Desktop) | PyInstaller + Inno Setup |

## Building from Source

### PC App
```bash
cd desktop
pip install -r requirements.txt

# Run directly
python -m phonebridge.main

# Build .exe
python build.py
```

### Android App
```bash
cd android
./gradlew assembleDebug
```

## Troubleshooting

### Phone not detected on PC
- Ensure both devices are on the **same Wi-Fi network**
- Check that **PhoneBridge is running** on your Android phone (green toggle)
- Try clicking **Rescan Network** in the PC app
- Check your firewall is not blocking **port 8273** or mDNS (port 5353)

### Mount fails or shows "Server Not Running"
- Verify the server is active on your phone (check the notification)
- Make sure **rclone** is installed and in your PATH
- Make sure **WinFsp** is installed (required for drive letter mapping)

### Wrong Password error
- The connection code is displayed on the phone screen — enter it exactly
- If the code was regenerated, the PC will prompt for the new one
- Try the **Change Password** option in the PC app to re-enter

### Drive letter not appearing in Explorer
- Wait a few seconds after mounting — rclone needs time to initialize
- Check if the drive appears in `This PC` or by navigating to `E:\` directly
- Try unmounting and re-mounting

### Remote access not working
- Make sure both devices have the VPN tunnel active (check the Tailscale app)
- Verify both devices are signed in with the **same account**
- Try connecting manually: click **🌐 Connect** in the PC app → enter the remote IP shown on your phone
- Check that PhoneBridge is running on your phone (green toggle)

### Slow transfer speeds
- Ensure you're on a **5GHz Wi-Fi** network (not 2.4GHz)
- Close other bandwidth-heavy applications
- Check the VFS cache mode in settings (set to "full" for best performance)
- For remote connections, speed depends on both devices' internet upload speed

## Roadmap

- [x] Phase 1: LAN mounting (same Wi-Fi) with HTTPS + Auth
- [x] Phase 2: Modern UI, live stats, selective folder sharing
- [x] Phase 3: Remote access with built-in VPN tunnel setup
- [ ] Phase 4: Linux & macOS PC support

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, and pull request guidelines.

## License

This project is licensed under the **GNU General Public License v3.0** — see the [LICENSE](LICENSE) file for details.

---

<p align="center">
  Made with ❤️ by <a href="https://github.com/ysachin26">Sachin Yadav</a>
</p>
