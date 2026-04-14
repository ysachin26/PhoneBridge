"""
PhoneBridge — Tailscale Auto-Discovery

Optional module that uses the Tailscale CLI to discover PhoneBridge
servers on the user's tailnet. Works alongside mDNS for LAN+VPN coverage.

Requires: Tailscale CLI installed and logged in on the PC.
"""

import json
import logging
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from typing import Callable, Optional

from .discovery import DiscoveredPhone

logger = logging.getLogger("phonebridge.tailscale")

# PhoneBridge default port
PHONEBRIDGE_PORT = 8273

# How often to scan tailnet peers (seconds)
SCAN_INTERVAL = 30

# Timeout for probing each peer (seconds)
PROBE_TIMEOUT = 3

# Cached path to the tailscale CLI binary
_tailscale_path: Optional[str] = None


def _find_tailscale() -> Optional[str]:
    """
    Find the Tailscale CLI binary.
    
    Checks PATH first, then common Windows install locations.
    Caches the result for subsequent calls.
    """
    global _tailscale_path
    if _tailscale_path is not None:
        return _tailscale_path if _tailscale_path else None
    
    # Check PATH first
    found = shutil.which("tailscale")
    if found:
        _tailscale_path = found
        return found
    
    # Check common Windows install paths
    if sys.platform == "win32":
        import os
        candidates = [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Tailscale", "tailscale.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tailscale", "tailscale.exe"),
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                _tailscale_path = path
                logger.info(f"Found Tailscale CLI at: {path}")
                return path
    
    _tailscale_path = ""  # Cache negative result (empty string = not found)
    return None


def is_tailscale_installed() -> bool:
    """Check if the Tailscale CLI is available."""
    return _find_tailscale() is not None


def get_tailscale_status() -> Optional[dict]:
    """
    Run `tailscale status --json` and parse the output.
    
    Returns the parsed JSON dict, or None if Tailscale is not
    installed, not running, or the command fails.
    """
    ts_bin = _find_tailscale()
    if not ts_bin:
        return None
    
    try:
        result = subprocess.run(
            [ts_bin, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            logger.debug(f"tailscale status exited with code {result.returncode}")
            return None
        
        return json.loads(result.stdout)
    except FileNotFoundError:
        logger.debug("Tailscale CLI not found in PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("tailscale status timed out")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse tailscale status JSON: {e}")
        return None
    except Exception as e:
        logger.debug(f"tailscale status error: {e}")
        return None


def _probe_phonebridge(ip: str, port: int = PHONEBRIDGE_PORT) -> Optional[dict]:
    """
    Probe a host to check if PhoneBridge is running.
    
    Attempts an unauthenticated request to /phonebridge/status.
    Returns the status JSON if it responds (even with 401), or None.
    """
    url = f"https://{ip}:{port}/phonebridge/status"
    req = urllib.request.Request(url, method="GET")
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            return data
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # Server is there but requires auth — that's PhoneBridge!
            return {"version": "unknown", "auth_required": True}
        return None
    except Exception:
        # Also try HTTP fallback
        try:
            url_http = f"http://{ip}:{port}/phonebridge/status"
            req_http = urllib.request.Request(url_http, method="GET")
            with urllib.request.urlopen(req_http, timeout=PROBE_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                return data
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {"version": "unknown", "auth_required": True, "protocol": "http"}
            return None
        except Exception:
            return None


def find_phonebridge_peers() -> list[DiscoveredPhone]:
    """
    Scan all Tailscale peers for running PhoneBridge instances.
    
    For each online peer in the tailnet, probes the PhoneBridge port
    to check if it's running. Returns a list of DiscoveredPhone objects
    for peers that respond.
    """
    status = get_tailscale_status()
    if not status:
        return []
    
    peers = status.get("Peer", {})
    if not peers:
        return []
    
    found = []
    
    for peer_id, peer_info in peers.items():
        # Only probe online peers
        if not peer_info.get("Online", False):
            continue
        
        # Get peer's Tailscale IP (first address)
        addresses = peer_info.get("TailscaleIPs", [])
        if not addresses:
            continue
        
        ip = addresses[0]
        hostname = peer_info.get("HostName", "")
        os_name = peer_info.get("OS", "")
        
        # Only probe Android devices (optimization: skip obvious non-phones)
        # But also probe unknown OS since some devices don't report OS
        if os_name and os_name.lower() not in ("android", "linux", ""):
            continue
        
        logger.debug(f"Probing Tailscale peer: {hostname} ({ip})")
        
        result = _probe_phonebridge(ip)
        if result:
            protocol = result.get("protocol", "https")
            version = result.get("version", "")
            
            phone = DiscoveredPhone(
                service_name=f"tailscale_{ip}_{PHONEBRIDGE_PORT}",
                display_name=hostname or f"Phone ({ip})",
                ip_address=ip,
                port=PHONEBRIDGE_PORT,
                device_model=f"Tailscale · {os_name}" if os_name else "Tailscale",
                version=version,
                auth_required=True,
                auth_user="phonebridge",
                protocol=protocol,
                tailscale_ip=ip,
                connection_type="tailscale",
            )
            
            logger.info(f"🌐 Found PhoneBridge on Tailscale peer: {phone}")
            found.append(phone)
    
    return found


class TailscaleScanner:
    """
    Background scanner that periodically discovers PhoneBridge
    servers on the user's Tailscale network.
    
    Usage:
        scanner = TailscaleScanner(on_found=handle_found)
        scanner.start()
        ...
        scanner.stop()
    """
    
    def __init__(
        self,
        on_found: Optional[Callable[[DiscoveredPhone], None]] = None,
        on_lost: Optional[Callable[[str], None]] = None,
        scan_interval: int = SCAN_INTERVAL,
    ):
        self._on_found = on_found
        self._on_lost = on_lost
        self._scan_interval = scan_interval
        self._known_peers: dict[str, DiscoveredPhone] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def start(self):
        """Start the background Tailscale scanning thread."""
        if self._running:
            return
        
        if not is_tailscale_installed():
            logger.info("Tailscale CLI not found — Tailscale discovery disabled")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        logger.info("Tailscale scanner started")
    
    def stop(self):
        """Stop the background scanning."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Tailscale scanner stopped")
    
    def get_phones(self) -> dict[str, DiscoveredPhone]:
        """Get currently known Tailscale PhoneBridge peers."""
        return dict(self._known_peers)
    
    def is_running(self) -> bool:
        return self._running
    
    def _scan_loop(self):
        """Main scan loop — runs in a background thread."""
        while self._running:
            try:
                self._do_scan()
            except Exception as e:
                logger.error(f"Tailscale scan error: {e}")
            
            # Wait for next scan interval (check every second for stop)
            for _ in range(self._scan_interval):
                if not self._running:
                    break
                time.sleep(1)
    
    def _do_scan(self):
        """Perform a single scan of Tailscale peers."""
        found_phones = find_phonebridge_peers()
        found_ids = {p.device_id for p in found_phones}
        
        # Check for new phones
        for phone in found_phones:
            if phone.device_id not in self._known_peers:
                self._known_peers[phone.device_id] = phone
                if self._on_found:
                    self._on_found(phone)
            else:
                # Update existing entry (IP might have changed)
                self._known_peers[phone.device_id] = phone
        
        # Check for lost phones
        lost_ids = set(self._known_peers.keys()) - found_ids
        for device_id in lost_ids:
            del self._known_peers[device_id]
            if self._on_lost:
                self._on_lost(device_id)
