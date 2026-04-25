"""
Tests for phonebridge.tailscale module.

Covers: _find_tailscale, is_tailscale_installed, get_tailscale_status,
        find_phonebridge_peers, TailscaleScanner lifecycle.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

import phonebridge.tailscale as ts_mod
from phonebridge.tailscale import (
    is_tailscale_installed,
    get_tailscale_status,
    find_phonebridge_peers,
    TailscaleScanner,
    _find_tailscale,
    PHONEBRIDGE_PORT,
)


@pytest.fixture(autouse=True)
def reset_cached_path():
    """Reset the cached tailscale path before each test."""
    ts_mod._tailscale_path = None
    yield
    ts_mod._tailscale_path = None


# ─── _find_tailscale ─────────────────────────────────────────────────

class TestFindTailscale:

    @patch("shutil.which", return_value="/usr/bin/tailscale")
    def test_found_in_path(self, mock_which):
        """Should return the path if tailscale is in PATH."""
        result = _find_tailscale()
        assert result == "/usr/bin/tailscale"

    @patch("shutil.which", return_value=None)
    @patch("sys.platform", "linux")
    def test_not_found_on_linux(self, mock_which):
        """Should return None if not in PATH and not Windows."""
        result = _find_tailscale()
        assert result is None

    def test_caches_positive_result(self):
        """Should cache a found path and not re-check."""
        ts_mod._tailscale_path = "/cached/tailscale"
        result = _find_tailscale()
        assert result == "/cached/tailscale"

    def test_caches_negative_result(self):
        """Should cache a not-found result (empty string)."""
        ts_mod._tailscale_path = ""
        result = _find_tailscale()
        assert result is None


class TestIsTailscaleInstalled:

    @patch("shutil.which", return_value="/usr/bin/tailscale")
    def test_installed(self, _):
        assert is_tailscale_installed() is True

    @patch("shutil.which", return_value=None)
    @patch("sys.platform", "linux")
    def test_not_installed(self, _):
        assert is_tailscale_installed() is False


# ─── get_tailscale_status ────────────────────────────────────────────

MOCK_STATUS_JSON = {
    "BackendState": "Running",
    "Self": {
        "TailscaleIPs": ["100.81.241.25"],
        "HostName": "mypc",
    },
    "Peer": {
        "abc123": {
            "HostName": "Redmi 12C",
            "TailscaleIPs": ["100.111.71.58"],
            "OS": "android",
            "Online": True,
        },
        "def456": {
            "HostName": "WorkLaptop",
            "TailscaleIPs": ["100.50.0.1"],
            "OS": "windows",
            "Online": True,
        },
        "ghi789": {
            "HostName": "OfflinePhone",
            "TailscaleIPs": ["100.200.0.1"],
            "OS": "android",
            "Online": False,
        },
    },
}


class TestGetTailscaleStatus:

    @patch("phonebridge.tailscale._find_tailscale", return_value="/usr/bin/tailscale")
    @patch("subprocess.run")
    def test_parses_json(self, mock_run, _):
        """Should parse tailscale status --json output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(MOCK_STATUS_JSON),
        )
        result = get_tailscale_status()
        assert result is not None
        assert result["BackendState"] == "Running"
        assert len(result["Peer"]) == 3

    @patch("phonebridge.tailscale._find_tailscale", return_value=None)
    def test_returns_none_when_not_installed(self, _):
        """Should return None if tailscale is not installed."""
        result = get_tailscale_status()
        assert result is None

    @patch("phonebridge.tailscale._find_tailscale", return_value="/usr/bin/tailscale")
    @patch("subprocess.run")
    def test_returns_none_on_nonzero_exit(self, mock_run, _):
        """Should return None if tailscale exits with error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = get_tailscale_status()
        assert result is None

    @patch("phonebridge.tailscale._find_tailscale", return_value="/usr/bin/tailscale")
    @patch("subprocess.run")
    def test_returns_none_on_bad_json(self, mock_run, _):
        """Should return None on invalid JSON output."""
        mock_run.return_value = MagicMock(returncode=0, stdout="NOT JSON")
        result = get_tailscale_status()
        assert result is None

    @patch("phonebridge.tailscale._find_tailscale", return_value="/usr/bin/tailscale")
    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_returns_none_on_file_not_found(self, _, __):
        """Should return None if binary disappeared."""
        result = get_tailscale_status()
        assert result is None


# ─── find_phonebridge_peers ──────────────────────────────────────────

class TestFindPhonebridgePeers:

    @patch("phonebridge.tailscale.get_tailscale_status", return_value=None)
    def test_returns_empty_when_no_status(self, _):
        """Should return [] when tailscale status fails."""
        result = find_phonebridge_peers()
        assert result == []

    @patch("phonebridge.tailscale.get_tailscale_status", return_value={"Peer": {}})
    def test_returns_empty_with_no_peers(self, _):
        """Should return [] when there are no peers."""
        result = find_phonebridge_peers()
        assert result == []

    @patch("phonebridge.tailscale._probe_phonebridge")
    @patch("phonebridge.tailscale.get_tailscale_status", return_value=MOCK_STATUS_JSON)
    def test_probes_android_peers(self, _, mock_probe):
        """Should probe Android peers and skip Windows peers."""
        mock_probe.return_value = {"version": "1"}
        
        result = find_phonebridge_peers()
        
        # Should have probed the online Android peer (Redmi 12C)
        # But NOT the offline one or the Windows one
        probed_ips = [c.args[0] for c in mock_probe.call_args_list]
        assert "100.111.71.58" in probed_ips
        assert "100.200.0.1" not in probed_ips  # Offline
        assert "100.50.0.1" not in probed_ips   # Windows

    @patch("phonebridge.tailscale._probe_phonebridge", return_value=None)
    @patch("phonebridge.tailscale.get_tailscale_status", return_value=MOCK_STATUS_JSON)
    def test_skips_non_phonebridge_peers(self, _, __):
        """Should return [] when no peers have PhoneBridge running."""
        result = find_phonebridge_peers()
        assert result == []

    @patch("phonebridge.tailscale._probe_phonebridge")
    @patch("phonebridge.tailscale.get_tailscale_status", return_value=MOCK_STATUS_JSON)
    def test_creates_discovered_phone(self, _, mock_probe):
        """Found peers should be returned as DiscoveredPhone objects."""
        mock_probe.return_value = {"version": "2", "auth_required": True}
        
        result = find_phonebridge_peers()
        assert len(result) >= 1
        
        phone = result[0]
        assert phone.ip_address == "100.111.71.58"
        assert phone.port == PHONEBRIDGE_PORT
        assert phone.connection_type == "tailscale"
        assert phone.tailscale_ip == "100.111.71.58"


# ─── TailscaleScanner ───────────────────────────────────────────────

class TestTailscaleScanner:

    @patch("phonebridge.tailscale.is_tailscale_installed", return_value=False)
    def test_start_noop_when_not_installed(self, _):
        """start() should be a no-op when tailscale is not installed."""
        scanner = TailscaleScanner()
        scanner.start()
        assert scanner.is_running() is False

    @patch("phonebridge.tailscale.is_tailscale_installed", return_value=True)
    @patch("phonebridge.tailscale.find_phonebridge_peers", return_value=[])
    def test_start_and_stop(self, _, __):
        """Scanner should start and stop cleanly."""
        scanner = TailscaleScanner(scan_interval=1)
        scanner.start()
        assert scanner.is_running() is True
        scanner.stop()
        assert scanner.is_running() is False

    def test_stop_without_start(self):
        """stop() should be safe to call without start()."""
        scanner = TailscaleScanner()
        scanner.stop()  # Should not raise
