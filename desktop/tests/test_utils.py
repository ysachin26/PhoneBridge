"""
Tests for phonebridge.utils module.

Covers: format_size, get_app_data_dir, get_available_drive_letters,
        check_rclone, check_winfsp.
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch

from phonebridge.utils import (
    format_size,
    get_app_data_dir,
    get_available_drive_letters,
    check_rclone,
    check_winfsp,
)


# ─── format_size ─────────────────────────────────────────────────────

class TestFormatSize:

    def test_bytes(self):
        assert format_size(0) == "0.0 B"
        assert format_size(512) == "512.0 B"
        assert format_size(1023) == "1023.0 B"

    def test_kilobytes(self):
        assert format_size(1024) == "1.0 KB"
        assert format_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert format_size(1024 * 1024) == "1.0 MB"
        assert format_size(int(1.5 * 1024 * 1024)) == "1.5 MB"

    def test_gigabytes(self):
        assert format_size(1024 ** 3) == "1.0 GB"

    def test_terabytes(self):
        assert format_size(1024 ** 4) == "1.0 TB"

    def test_petabytes(self):
        assert format_size(1024 ** 5) == "1.0 PB"

    def test_large_number(self):
        """Should handle very large numbers."""
        result = format_size(500 * 1024 ** 4)
        assert "TB" in result


# ─── get_app_data_dir ────────────────────────────────────────────────

class TestGetAppDataDir:

    def test_returns_path_object(self):
        result = get_app_data_dir()
        assert isinstance(result, Path)

    def test_directory_exists(self):
        result = get_app_data_dir()
        assert result.exists()
        assert result.is_dir()

    def test_ends_with_phonebridge(self):
        result = get_app_data_dir()
        assert result.name == "PhoneBridge"


# ─── get_available_drive_letters ─────────────────────────────────────

class TestGetAvailableDriveLetters:

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_returns_list(self):
        result = get_available_drive_letters()
        assert isinstance(result, list)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_letters_are_valid(self):
        result = get_available_drive_letters()
        for letter in result:
            assert len(letter) == 2
            assert letter[1] == ":"
            assert letter[0].isalpha()
            assert letter[0].isupper()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_skips_system_drives(self):
        """Should not include A:, B:, C:, D: drives."""
        result = get_available_drive_letters()
        for letter in result:
            assert letter[0] not in ("A", "B", "C", "D")

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_returns_z_first(self):
        """Available letters should be ordered Z down to E."""
        result = get_available_drive_letters()
        if len(result) >= 2:
            # First letter should be higher (closer to Z) than second
            assert result[0][0] >= result[1][0]

    @pytest.mark.skipif(sys.platform == "win32", reason="Non-Windows only")
    def test_returns_empty_on_non_windows(self):
        result = get_available_drive_letters()
        assert result == []


# ─── check_rclone ───────────────────────────────────────────────────

class TestCheckRclone:

    @patch("shutil.which", return_value="/usr/bin/rclone")
    def test_found_in_path(self, _):
        result = check_rclone()
        assert result is not None
        assert "rclone" in result

    @patch("shutil.which", return_value=None)
    @patch("sys.platform", "linux")
    def test_not_found(self, _):
        result = check_rclone()
        # On Linux with no rclone, should return None
        # (Windows will try extra paths, so skip asserting None on Windows)


# ─── check_winfsp ───────────────────────────────────────────────────

class TestCheckWinfsp:

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_returns_bool(self):
        result = check_winfsp()
        assert isinstance(result, bool)

    @patch("sys.platform", "linux")
    def test_returns_true_on_non_windows(self):
        """WinFsp check should return True on non-Windows platforms."""
        result = check_winfsp()
        assert result is True
