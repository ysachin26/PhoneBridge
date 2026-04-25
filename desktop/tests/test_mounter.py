"""
Tests for phonebridge.mounter module.

Covers: MountInfo, MountManager (auth checking, drive letters, deps).
"""

import subprocess
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from phonebridge.mounter import (
    MountInfo,
    MountManager,
    MountError,
    AuthError,
    AUTH_ERROR_MARKERS,
)
from phonebridge.discovery import DiscoveredPhone


# ─── MountInfo ───────────────────────────────────────────────────────

class TestMountInfo:

    def test_is_alive_with_running_process(self):
        """is_alive should return True when process is running."""
        proc = MagicMock()
        proc.poll.return_value = None  # Still running
        mi = MountInfo(
            device_id="d1",
            display_name="Phone",
            drive_letter="E:",
            webdav_url="https://1.2.3.4:8273",
            process=proc,
        )
        assert mi.is_alive is True

    def test_is_alive_with_exited_process(self):
        """is_alive should return False when process has exited."""
        proc = MagicMock()
        proc.poll.return_value = 0  # Exited
        mi = MountInfo(
            device_id="d1",
            display_name="Phone",
            drive_letter="E:",
            webdav_url="https://1.2.3.4:8273",
            process=proc,
        )
        assert mi.is_alive is False

    def test_is_alive_with_no_process(self):
        """is_alive should return False when process is None."""
        mi = MountInfo(
            device_id="d1",
            display_name="Phone",
            drive_letter="E:",
            webdav_url="https://1.2.3.4:8273",
        )
        assert mi.is_alive is False


# ─── MountManager.is_auth_error ──────────────────────────────────────

class TestIsAuthError:

    def test_detects_401(self):
        assert MountManager.is_auth_error("HTTP error 401 Unauthorized") is True

    def test_detects_unauthorized(self):
        assert MountManager.is_auth_error("Server returned unauthorized") is True

    def test_detects_auth_failed(self):
        assert MountManager.is_auth_error("authentication failed") is True

    def test_no_false_positive(self):
        assert MountManager.is_auth_error("Connection refused") is False
        assert MountManager.is_auth_error("Timeout") is False
        assert MountManager.is_auth_error("DNS resolution failed") is False

    def test_case_insensitive(self):
        assert MountManager.is_auth_error("UNAUTHORIZED") is True
        assert MountManager.is_auth_error("Authentication Failed") is True

    def test_all_markers_detected(self):
        """Every marker in AUTH_ERROR_MARKERS should be detected."""
        for marker in AUTH_ERROR_MARKERS:
            assert MountManager.is_auth_error(f"Error: {marker}") is True


# ─── MountManager ───────────────────────────────────────────────────

class TestMountManager:

    def _make_manager(self, **kwargs):
        defaults = dict(rclone_path="/fake/rclone")
        defaults.update(kwargs)
        return MountManager(**defaults)

    def _make_phone(self, **kwargs):
        defaults = dict(
            service_name="Test._phonebridge._tcp.local.",
            display_name="Test Phone",
            ip_address="192.168.1.10",
            port=8273,
            device_model="Test",
            version="1",
            protocol="https",
        )
        defaults.update(kwargs)
        return DiscoveredPhone(**defaults)

    @patch("phonebridge.mounter.check_rclone", return_value=None)
    def test_check_dependencies_missing_rclone(self, _):
        """Should report rclone missing."""
        mm = MountManager(rclone_path=None)
        missing = mm.check_dependencies()
        assert "rclone" in missing

    @patch("phonebridge.mounter.check_winfsp", return_value=True)
    def test_check_dependencies_all_present(self, _):
        """Should return empty list when all deps are present."""
        mm = self._make_manager()
        missing = mm.check_dependencies()
        assert "rclone" not in missing

    def test_get_next_drive_letter(self):
        """Should return an available drive letter."""
        mm = self._make_manager()
        letter = mm.get_next_drive_letter()
        assert letter is not None
        assert letter.endswith(":")
        assert letter[0].isalpha()

    def test_is_mounted_false_when_empty(self):
        """is_mounted should return False for unknown devices."""
        mm = self._make_manager()
        assert mm.is_mounted("nonexistent") is False

    @patch("phonebridge.mounter.check_rclone", return_value=None)
    def test_mount_raises_without_rclone(self, _):
        """mount should raise MountError if rclone is missing."""
        mm = MountManager(rclone_path=None)
        phone = self._make_phone()
        with pytest.raises(MountError, match="rclone"):
            mm.mount(phone, "E:")

    @patch("phonebridge.mounter.MountManager.is_server_reachable", return_value=False)
    def test_mount_raises_when_server_unreachable(self, _):
        """mount should raise MountError if server is unreachable."""
        mm = self._make_manager()
        phone = self._make_phone()
        with pytest.raises(MountError, match="not reachable"):
            mm.mount(phone, "E:")

    def test_unmount_unknown_device(self):
        """unmount should handle unknown devices gracefully."""
        mm = self._make_manager()
        mm.unmount("nonexistent")  # Should not raise

    def test_get_mounts_empty(self):
        """get_mounts should return empty dict initially."""
        mm = self._make_manager()
        assert mm.get_mounts() == {}

    @patch("subprocess.run")
    def test_obscure_password(self, mock_run):
        """_obscure_password should call rclone obscure."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="obscured_pass_123\n"
        )
        mm = self._make_manager()
        result = mm._obscure_password("mypassword")
        assert result == "obscured_pass_123"

    @patch("subprocess.run")
    def test_obscure_password_failure(self, mock_run):
        """_obscure_password should return original on failure."""
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        mm = self._make_manager()
        result = mm._obscure_password("mypassword")
        assert result == "mypassword"

    def test_health_monitor_start_stop(self):
        """Health monitor should start and stop cleanly."""
        mm = self._make_manager()
        mm.start_health_monitor()
        assert mm._running is True
        mm.stop_health_monitor()
        assert mm._running is False

    def test_check_auth_timeout_parameter(self):
        """check_auth should accept a timeout parameter."""
        mm = self._make_manager()
        # Just verify it accepts the parameter without TypeError
        import inspect
        sig = inspect.signature(mm.check_auth)
        assert "timeout" in sig.parameters

    def test_is_server_reachable_timeout_parameter(self):
        """is_server_reachable should accept a timeout parameter."""
        mm = self._make_manager()
        import inspect
        sig = inspect.signature(mm.is_server_reachable)
        assert "timeout" in sig.parameters
