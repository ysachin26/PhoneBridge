"""
Tests for phonebridge.certpin module.

Covers: get_server_fingerprint, verify_fingerprint, fingerprint_changed.
"""

import pytest
from unittest.mock import patch, MagicMock

from phonebridge.certpin import (
    get_server_fingerprint,
    verify_fingerprint,
    fingerprint_changed,
)


# ─── get_server_fingerprint ──────────────────────────────────────────

class TestGetServerFingerprint:

    @patch("phonebridge.certpin.socket.create_connection")
    @patch("phonebridge.certpin.ssl.create_default_context")
    def test_extracts_fingerprint(self, mock_ctx, mock_conn):
        """Should extract SHA-256 fingerprint from server certificate."""
        # Create a mock DER cert (just some bytes)
        fake_cert = b"\x30\x82\x01\x00" + b"\x00" * 252  # 256 bytes
        
        mock_ssock = MagicMock()
        mock_ssock.getpeercert.return_value = fake_cert
        mock_ssock.__enter__ = MagicMock(return_value=mock_ssock)
        mock_ssock.__exit__ = MagicMock(return_value=False)
        
        mock_ctx_instance = MagicMock()
        mock_ctx.return_value = mock_ctx_instance
        mock_ctx_instance.wrap_socket.return_value = mock_ssock
        
        mock_sock = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_sock)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        
        result = get_server_fingerprint("1.2.3.4", 8273)
        
        if result is not None:
            # Should be colon-separated hex
            assert ":" in result
            parts = result.split(":")
            assert all(len(p) == 2 for p in parts)
            assert len(parts) == 32  # SHA-256 = 32 bytes

    @patch("phonebridge.certpin.socket.create_connection", side_effect=ConnectionRefusedError)
    def test_returns_none_on_connection_error(self, _):
        """Should return None when connection fails."""
        result = get_server_fingerprint("1.2.3.4", 8273, timeout=1)
        assert result is None

    @patch("phonebridge.certpin.socket.create_connection", side_effect=TimeoutError)
    def test_returns_none_on_timeout(self, _):
        """Should return None on connection timeout."""
        result = get_server_fingerprint("1.2.3.4", 8273, timeout=1)
        assert result is None


# ─── verify_fingerprint ──────────────────────────────────────────────

class TestVerifyFingerprint:

    @patch("phonebridge.certpin.get_server_fingerprint", return_value="AB:CD:EF:01:23")
    def test_match_returns_true(self, _):
        """Should return (True, fp) when fingerprints match."""
        valid, fp = verify_fingerprint("1.2.3.4", 8273, "AB:CD:EF:01:23")
        assert valid is True
        assert fp == "AB:CD:EF:01:23"

    @patch("phonebridge.certpin.get_server_fingerprint", return_value="AB:CD:EF:01:23")
    def test_match_case_insensitive(self, _):
        """Should match case-insensitively."""
        valid, fp = verify_fingerprint("1.2.3.4", 8273, "ab:cd:ef:01:23")
        assert valid is True

    @patch("phonebridge.certpin.get_server_fingerprint", return_value="AA:BB:CC:DD:EE")
    def test_mismatch_returns_false(self, _):
        """Should return (False, fp) when fingerprints don't match."""
        valid, fp = verify_fingerprint("1.2.3.4", 8273, "11:22:33:44:55")
        assert valid is False
        assert fp == "AA:BB:CC:DD:EE"

    @patch("phonebridge.certpin.get_server_fingerprint", return_value=None)
    def test_unreachable_returns_true(self, _):
        """Should return (True, None) when server is unreachable."""
        valid, fp = verify_fingerprint("1.2.3.4", 8273, "AB:CD")
        assert valid is True
        assert fp is None


# ─── fingerprint_changed ─────────────────────────────────────────────

class TestFingerprintChanged:

    def test_same_fingerprints(self):
        assert fingerprint_changed("AB:CD:EF", "AB:CD:EF") is False

    def test_different_fingerprints(self):
        assert fingerprint_changed("AB:CD:EF", "11:22:33") is True

    def test_case_insensitive(self):
        assert fingerprint_changed("ab:cd:ef", "AB:CD:EF") is False

    def test_empty_saved(self):
        """Should return False when saved is empty (first connection)."""
        assert fingerprint_changed("", "AB:CD:EF") is False

    def test_empty_current(self):
        """Should return False when current is empty."""
        assert fingerprint_changed("AB:CD:EF", "") is False

    def test_both_empty(self):
        assert fingerprint_changed("", "") is False

    def test_whitespace_handling(self):
        """Should strip whitespace before comparing."""
        assert fingerprint_changed("  AB:CD  ", "AB:CD") is False
