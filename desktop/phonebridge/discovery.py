"""
PhoneBridge — mDNS Service Discovery

Uses the zeroconf library to discover PhoneBridge servers 
(Android phones running the WebDAV server) on the local network.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf, ServiceInfo

logger = logging.getLogger("phonebridge.discovery")

# PhoneBridge mDNS service type
SERVICE_TYPE = "_phonebridge._tcp.local."


@dataclass
class DiscoveredPhone:
    """Represents a phone discovered on the network."""
    service_name: str       # mDNS service name
    display_name: str       # Friendly name from TXT record
    ip_address: str         # Resolved IP address
    port: int               # Server port
    device_model: str       # Phone model from TXT record
    version: str            # PhoneBridge server version
    auth_required: bool = True   # Whether Basic Auth is required
    auth_user: str = "phonebridge"  # Username for Basic Auth
    protocol: str = "https"      # "http" or "https"
    tailscale_ip: str = ""       # Tailscale IP (from mDNS TXT or status API)
    connection_type: str = "auto"  # "auto" or "manual"

    @property
    def device_id(self) -> str:
        """Unique identifier derived from service name."""
        if self.connection_type == "manual":
            return f"manual_{self.ip_address}_{self.port}"
        return self.service_name.replace(f".{SERVICE_TYPE}", "").strip()

    @property
    def webdav_url(self) -> str:
        """Full WebDAV URL for rclone."""
        return f"{self.protocol}://{self.ip_address}:{self.port}"

    @classmethod
    def create_manual(cls, ip_address: str, port: int = 8273,
                      protocol: str = "https",
                      display_name: str = "",
                      password: str = "") -> "DiscoveredPhone":
        """
        Factory method to create a DiscoveredPhone from manual IP input.
        
        Used when connecting to a phone that isn't on the local network
        (e.g., via Tailscale or any VPN).
        """
        if not display_name:
            display_name = f"Phone ({ip_address})"
        
        return cls(
            service_name=f"manual_{ip_address}_{port}",
            display_name=display_name,
            ip_address=ip_address,
            port=port,
            device_model="Manual Connection",
            version="",
            auth_required=True,
            auth_user="phonebridge",
            protocol=protocol,
            connection_type="manual",
        )

    def __str__(self):
        auth_status = "🔒" if self.auth_required else "🔓"
        remote = f" 🌐{self.tailscale_ip}" if self.tailscale_ip else ""
        return f"{self.display_name} ({self.protocol}://{self.ip_address}:{self.port}) {auth_status}{remote}"


class PhoneDiscoveryListener(ServiceListener):
    """Listener for PhoneBridge mDNS service events."""

    def __init__(
        self,
        on_found: Optional[Callable[[DiscoveredPhone], None]] = None,
        on_lost: Optional[Callable[[str], None]] = None,
        on_updated: Optional[Callable[[DiscoveredPhone], None]] = None,
    ):
        self._on_found = on_found
        self._on_lost = on_lost
        self._on_updated = on_updated
        self._lock = threading.Lock()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a new PhoneBridge service is found."""
        logger.info(f"Service found: {name}")
        phone = self._resolve_service(zc, type_, name)
        if phone and self._on_found:
            self._on_found(phone)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a PhoneBridge service disappears."""
        device_id = name.replace(f".{SERVICE_TYPE}", "").strip()
        logger.info(f"Service lost: {device_id}")
        if self._on_lost:
            self._on_lost(device_id)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a PhoneBridge service is updated."""
        logger.debug(f"Service updated: {name}")
        phone = self._resolve_service(zc, type_, name)
        if phone and self._on_updated:
            self._on_updated(phone)

    def _resolve_service(self, zc: Zeroconf, type_: str, name: str) -> Optional[DiscoveredPhone]:
        """Resolve a service to get its IP, port, and metadata."""
        info = zc.get_service_info(type_, name, timeout=5000)
        if info is None:
            logger.warning(f"Could not resolve service: {name}")
            return None

        # Get the first IPv4 address
        addresses = info.parsed_addresses()
        if not addresses:
            logger.warning(f"No addresses found for service: {name}")
            return None

        ip = addresses[0]
        port = info.port

        # Parse TXT records
        properties = {}
        if info.properties:
            for key, value in info.properties.items():
                k = key.decode("utf-8") if isinstance(key, bytes) else key
                v = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                properties[k] = v

        display_name = properties.get("deviceName", name.split(".")[0])
        device_model = properties.get("model", "Unknown")
        version = properties.get("version", "0")
        auth_required = properties.get("auth_required", "true").lower() == "true"
        auth_user = properties.get("auth_user", "phonebridge")
        protocol = properties.get("protocol", "http")
        tailscale_ip = properties.get("tailscale_ip", "")

        phone = DiscoveredPhone(
            service_name=name,
            display_name=display_name,
            ip_address=ip,
            port=port,
            device_model=device_model,
            version=version,
            auth_required=auth_required,
            auth_user=auth_user,
            protocol=protocol,
            tailscale_ip=tailscale_ip,
        )

        logger.info(f"Resolved: {phone}")
        return phone


class PhoneScanner:
    """
    Manages mDNS discovery of PhoneBridge servers on the local network.
    
    Usage:
        scanner = PhoneScanner(on_found=handle_found, on_lost=handle_lost)
        scanner.start()
        ...
        scanner.stop()
    """

    def __init__(
        self,
        on_found: Optional[Callable[[DiscoveredPhone], None]] = None,
        on_lost: Optional[Callable[[str], None]] = None,
        on_updated: Optional[Callable[[DiscoveredPhone], None]] = None,
    ):
        self._on_found = on_found
        self._on_lost = on_lost
        self._on_updated = on_updated

        self._zeroconf: Optional[Zeroconf] = None
        self._browser: Optional[ServiceBrowser] = None
        self._phones: dict[str, DiscoveredPhone] = {}
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        """Start scanning for PhoneBridge services."""
        if self._running:
            logger.warning("Scanner already running")
            return

        logger.info("Starting mDNS scanner...")
        try:
            self._zeroconf = Zeroconf()
            listener = PhoneDiscoveryListener(
                on_found=self._handle_found,
                on_lost=self._handle_lost,
                on_updated=self._handle_updated,
            )
            self._browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE, listener)
            self._running = True
            logger.info(f"Scanner active — listening for {SERVICE_TYPE}")
        except Exception as e:
            logger.error(f"Failed to start scanner: {e}")
            self.stop()

    def stop(self):
        """Stop scanning."""
        self._running = False
        if self._browser:
            self._browser.cancel()
            self._browser = None
        if self._zeroconf:
            self._zeroconf.close()
            self._zeroconf = None
        logger.info("Scanner stopped")

    def get_phones(self) -> dict[str, DiscoveredPhone]:
        """Get all currently discovered phones."""
        with self._lock:
            return dict(self._phones)

    def is_running(self) -> bool:
        return self._running

    def _handle_found(self, phone: DiscoveredPhone):
        with self._lock:
            # Deduplicate by IP: if a phone with the same IP exists but has a different ID
            # (e.g. from mDNS renaming due to collision like 'Xiaomi (1)'), remove the old one.
            for old_id, p in list(self._phones.items()):
                if p.ip_address == phone.ip_address and old_id != phone.device_id:
                    logger.info(f"Removing stale mDNS duplicate for {p.display_name} (same IP)")
                    self._phones.pop(old_id, None)
                    if self._on_lost:
                        # Call on_lost outside lock
                        threading.Thread(target=self._on_lost, args=(old_id,), daemon=True).start()

            self._phones[phone.device_id] = phone
            
        logger.info(f"📱 Phone discovered: {phone}")
        if self._on_found:
            self._on_found(phone)

    def _handle_lost(self, device_id: str):
        with self._lock:
            removed = self._phones.pop(device_id, None)
        if removed:
            logger.info(f"📱 Phone lost: {removed.display_name}")
        if self._on_lost:
            self._on_lost(device_id)

    def _handle_updated(self, phone: DiscoveredPhone):
        with self._lock:
            self._phones[phone.device_id] = phone
        if self._on_updated:
            self._on_updated(phone)
