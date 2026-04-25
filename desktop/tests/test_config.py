"""
Tests for phonebridge.config module.

Covers: PhoneConfig, AppConfig, ConfigManager (load/save/upsert/remove).
"""

import json
import pytest
from pathlib import Path
from phonebridge.config import PhoneConfig, AppConfig, ConfigManager


# ─── PhoneConfig ─────────────────────────────────────────────────────

class TestPhoneConfig:

    def test_defaults(self):
        """PhoneConfig should have sensible defaults."""
        pc = PhoneConfig(device_id="test1", display_name="Test Phone")
        assert pc.device_id == "test1"
        assert pc.display_name == "Test Phone"
        assert pc.last_port == 8273
        assert pc.auto_mount is True
        assert pc.connection_type == "auto"
        assert pc.protocol == "https"
        assert pc.auth_user == "phonebridge"

    def test_round_trip(self):
        """to_dict → from_dict should produce an identical PhoneConfig."""
        original = PhoneConfig(
            device_id="d1",
            display_name="Pixel 7",
            last_ip="192.168.1.5",
            last_port=8273,
            preferred_drive="E:",
            auth_password="secret123",
            connection_type="manual",
            protocol="http",
        )
        d = original.to_dict()
        restored = PhoneConfig.from_dict(d)
        assert restored.device_id == original.device_id
        assert restored.display_name == original.display_name
        assert restored.last_ip == original.last_ip
        assert restored.preferred_drive == original.preferred_drive
        assert restored.auth_password == original.auth_password
        assert restored.connection_type == "manual"
        assert restored.protocol == "http"

    def test_from_dict_ignores_extra_keys(self):
        """from_dict should silently ignore unknown fields."""
        data = {
            "device_id": "d2",
            "display_name": "Phone",
            "some_future_field": "value",
        }
        pc = PhoneConfig.from_dict(data)
        assert pc.device_id == "d2"
        assert not hasattr(pc, "some_future_field")

    def test_from_dict_missing_optional_keys(self):
        """from_dict with only required fields should use defaults."""
        data = {"device_id": "d3", "display_name": "Minimal"}
        pc = PhoneConfig.from_dict(data)
        assert pc.last_port == 8273
        assert pc.connection_type == "auto"


# ─── AppConfig ───────────────────────────────────────────────────────

class TestAppConfig:

    def test_defaults(self):
        """AppConfig should have sensible defaults."""
        ac = AppConfig()
        assert ac.version == 1
        assert ac.vfs_cache_mode == "full"
        assert ac.show_notifications is True
        assert ac.phones == {}

    def test_round_trip_with_phones(self):
        """to_dict → from_dict should preserve nested PhoneConfigs."""
        ac = AppConfig()
        ac.phones["d1"] = PhoneConfig(device_id="d1", display_name="Phone A")
        ac.phones["d2"] = PhoneConfig(
            device_id="d2", display_name="Phone B",
            connection_type="manual", protocol="http",
        )

        d = ac.to_dict()
        restored = AppConfig.from_dict(d)
        assert len(restored.phones) == 2
        assert restored.phones["d1"].display_name == "Phone A"
        assert restored.phones["d2"].connection_type == "manual"
        assert restored.phones["d2"].protocol == "http"

    def test_from_dict_with_extra_fields(self):
        """from_dict should skip unknown top-level fields."""
        d = {"version": 2, "unknown_key": True}
        ac = AppConfig.from_dict(d)
        assert ac.version == 2

    def test_from_dict_with_corrupt_phones(self):
        """from_dict should handle non-dict phone entries gracefully."""
        d = {"phones": {"bad_entry": "not a dict"}}
        ac = AppConfig.from_dict(d)
        assert len(ac.phones) == 0  # Skipped the bad entry


# ─── ConfigManager ───────────────────────────────────────────────────

class TestConfigManager:

    def test_load_creates_default_on_missing_file(self, tmp_path):
        """ConfigManager should create a default config if file doesn't exist."""
        config_file = tmp_path / "config.json"
        cm = ConfigManager(config_path=config_file)
        assert cm.config.version == 1
        assert len(cm.config.phones) == 0

    def test_save_and_reload(self, tmp_path):
        """Saved config should be loadable."""
        config_file = tmp_path / "config.json"
        cm = ConfigManager(config_path=config_file)
        cm.upsert_phone(PhoneConfig(device_id="p1", display_name="Test"))
        cm.save()

        # Reload from disk
        cm2 = ConfigManager(config_path=config_file)
        assert "p1" in cm2.config.phones
        assert cm2.config.phones["p1"].display_name == "Test"

    def test_upsert_phone(self, tmp_path):
        """upsert_phone should add and update phone configs."""
        cm = ConfigManager(config_path=tmp_path / "config.json")
        cm.upsert_phone(PhoneConfig(device_id="p1", display_name="V1"))
        assert cm.config.phones["p1"].display_name == "V1"

        cm.upsert_phone(PhoneConfig(device_id="p1", display_name="V2"))
        assert cm.config.phones["p1"].display_name == "V2"

    def test_remove_phone(self, tmp_path):
        """remove_phone should delete and persist the change."""
        cm = ConfigManager(config_path=tmp_path / "config.json")
        cm.upsert_phone(PhoneConfig(device_id="p1", display_name="T"))
        cm.remove_phone("p1")
        assert "p1" not in cm.config.phones

    def test_remove_nonexistent_phone(self, tmp_path):
        """remove_phone should be a no-op for unknown IDs."""
        cm = ConfigManager(config_path=tmp_path / "config.json")
        cm.remove_phone("nonexistent")  # Should not raise

    def test_get_phone(self, tmp_path):
        """get_phone should return the right phone or None."""
        cm = ConfigManager(config_path=tmp_path / "config.json")
        cm.upsert_phone(PhoneConfig(device_id="p1", display_name="T"))
        assert cm.get_phone("p1") is not None
        assert cm.get_phone("p2") is None

    def test_get_all_phones_returns_copy(self, tmp_path):
        """get_all_phones should return a copy, not a reference."""
        cm = ConfigManager(config_path=tmp_path / "config.json")
        cm.upsert_phone(PhoneConfig(device_id="p1", display_name="T"))
        all_phones = cm.get_all_phones()
        all_phones.clear()
        assert len(cm.config.phones) == 1  # Original unchanged

    def test_assign_drive_letter(self, tmp_path):
        """assign_drive_letter should update and save."""
        cm = ConfigManager(config_path=tmp_path / "config.json")
        cm.upsert_phone(PhoneConfig(device_id="p1", display_name="T"))
        cm.assign_drive_letter("p1", "Z:")
        assert cm.config.phones["p1"].preferred_drive == "Z:"

    def test_corrupted_config_file(self, tmp_path):
        """Corrupted JSON should fallback to defaults."""
        config_file = tmp_path / "config.json"
        config_file.write_text("NOT VALID JSON {{{")
        cm = ConfigManager(config_path=config_file)
        assert cm.config.version == 1  # Default

    def test_connection_type_persists(self, tmp_path):
        """connection_type='manual' should survive save/load cycle."""
        config_file = tmp_path / "config.json"
        cm = ConfigManager(config_path=config_file)
        cm.upsert_phone(PhoneConfig(
            device_id="m1",
            display_name="Remote Phone",
            connection_type="manual",
            protocol="http",
        ))
        cm.save()

        cm2 = ConfigManager(config_path=config_file)
        p = cm2.get_phone("m1")
        assert p.connection_type == "manual"
        assert p.protocol == "http"
