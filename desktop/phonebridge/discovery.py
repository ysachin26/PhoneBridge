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

    @property
    def device_id(self) -> str:
        """Unique identifier derived from service name."""
        return self.service_name.replace(f".{SERVICE_TYPE}", "").strip()

    @property
    def webdav_url(self) -> str:
        """Full WebDAV URL for rclone."""
        return f"{self.protocol}://{self.ip_address}:{self.port}"

    def __str__(self):
        auth_status = "🔒" if self.auth_required else "🔓"
        return f"{self.display_name} ({self.protocol}://{self.ip_address}:{self.port}) {auth_status}"


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
