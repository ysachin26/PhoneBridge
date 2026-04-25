"""
Tests for phonebridge.discovery module.

Covers: DiscoveredPhone, create_manual(), PhoneScanner callbacks.
"""

import threading
import pytest
from unittest.mock import MagicMock, patch

from phonebridge.discovery import DiscoveredPhone, PhoneScanner, SERVICE_TYPE


# ─── DiscoveredPhone ─────────────────────────────────────────────────

class TestDiscoveredPhone:

    def _make_phone(self, **kwargs):
        defaults = dict(
            service_name="Pixel 7._phonebridge._tcp.local.",
            display_name="Pixel 7",
            ip_address="192.168.1.10",
            port=8273,
            device_model="Pixel 7",
            version="1",
        )
        defaults.update(kwargs)
        return DiscoveredPhone(**defaults)

    def test_device_id_from_service_name(self):
        """device_id should strip the service type suffix."""
        phone = self._make_phone()
        assert phone.device_id == "Pixel 7"

    def test_device_id_manual(self):
        """Manual phones should have 'manual_<ip>_<port>' device IDs."""
        phone = DiscoveredPhone.create_manual(
            ip_address="100.64.0.2",
            port=8273,
        )
        assert phone.device_id == "manual_100.64.0.2_8273"

    def test_webdav_url_https(self):
        """webdav_url should respect the protocol."""
        phone = self._make_phone(protocol="https")
        assert phone.webdav_url == "https://192.168.1.10:8273"

    def test_webdav_url_http(self):
        phone = self._make_phone(protocol="http")
        assert phone.webdav_url == "http://192.168.1.10:8273"

    def test_create_manual_defaults(self):
        """create_manual should set correct defaults."""
        phone = DiscoveredPhone.create_manual(ip_address="10.0.0.1")
        assert phone.ip_address == "10.0.0.1"
        assert phone.port == 8273
        assert phone.protocol == "https"
        assert phone.connection_type == "manual"
        assert phone.auth_required is True
        assert phone.display_name == "Phone (10.0.0.1)"

    def test_create_manual_custom_name(self):
        """create_manual should accept a custom display name."""
        phone = DiscoveredPhone.create_manual(
            ip_address="10.0.0.1",
            display_name="My Remote Phone",
        )
        assert phone.display_name == "My Remote Phone"

    def test_create_manual_http_protocol(self):
        """create_manual should accept http protocol."""
        phone = DiscoveredPhone.create_manual(
            ip_address="10.0.0.1",
            protocol="http",
        )
        assert phone.protocol == "http"
        assert phone.webdav_url == "http://10.0.0.1:8273"

    def test_str_representation(self):
        """__str__ should include name, address, and lock icon."""
        phone = self._make_phone(auth_required=True)
        s = str(phone)
        assert "Pixel 7" in s
        assert "192.168.1.10" in s
        assert "🔒" in s

    def test_str_with_tailscale_ip(self):
        """__str__ should show tailscale IP when available."""
        phone = self._make_phone(tailscale_ip="100.64.0.5")
        s = str(phone)
        assert "100.64.0.5" in s

    def test_auto_connection_type_default(self):
        """Default connection_type should be 'auto'."""
        phone = self._make_phone()
        assert phone.connection_type == "auto"


# ─── PhoneScanner ────────────────────────────────────────────────────

class TestPhoneScanner:

    def _make_phone(self, name="Test", ip="192.168.1.1"):
        return DiscoveredPhone(
            service_name=f"{name}.{SERVICE_TYPE}",
            display_name=name,
            ip_address=ip,
            port=8273,
            device_model="Test",
            version="1",
        )

    def test_handle_found_adds_phone(self):
        """_handle_found should add a phone to the internal dict."""
        found_phones = []
        scanner = PhoneScanner(on_found=found_phones.append)
        phone = self._make_phone()
        scanner._handle_found(phone)

        assert phone.device_id in scanner.get_phones()
        assert len(found_phones) == 1

    def test_handle_lost_removes_phone(self):
        """_handle_lost should remove a phone from the internal dict."""
        lost_ids = []
        scanner = PhoneScanner(on_lost=lost_ids.append)
        phone = self._make_phone()
        scanner._handle_found(phone)
        scanner._handle_lost(phone.device_id)

        assert phone.device_id not in scanner.get_phones()
        assert phone.device_id in lost_ids

    def test_lost_unknown_phone(self):
        """_handle_lost for unknown phone should not crash."""
        lost_ids = []
        scanner = PhoneScanner(on_lost=lost_ids.append)
        scanner._handle_lost("nonexistent")
        assert "nonexistent" in lost_ids

    def test_dedup_by_ip(self):
        """If two services share an IP, the old one should be replaced."""
        lost_ids = []
        scanner = PhoneScanner(on_lost=lambda did: lost_ids.append(did))
        
        phone1 = self._make_phone(name="Phone1", ip="192.168.1.1")
        phone2 = self._make_phone(name="Phone2", ip="192.168.1.1")
        
        scanner._handle_found(phone1)
        scanner._handle_found(phone2)
        
        # Wait briefly for the dedup thread
        import time
        time.sleep(0.1)
        
        phones = scanner.get_phones()
        assert len(phones) == 1
        assert "Phone2" in phones

    def test_get_phones_returns_copy(self):
        """get_phones should return a copy, not a reference."""
        scanner = PhoneScanner()
        phone = self._make_phone()
        scanner._handle_found(phone)
        
        copy = scanner.get_phones()
        copy.clear()
        assert len(scanner.get_phones()) == 1

    def test_handle_updated(self):
        """_handle_updated should update existing phone data."""
        scanner = PhoneScanner()
        phone = self._make_phone(name="Phone", ip="192.168.1.1")
        scanner._handle_found(phone)
        
        # Update IP
        updated = self._make_phone(name="Phone", ip="192.168.1.2")
        scanner._handle_updated(updated)
        
        phones = scanner.get_phones()
        assert phones["Phone"].ip_address == "192.168.1.2"
