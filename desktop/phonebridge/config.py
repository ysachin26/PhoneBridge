"""
PhoneBridge — Configuration Management

Handles persistent storage of phone configs, drive letter assignments,
and app preferences.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .utils import get_app_data_dir

logger = logging.getLogger("phonebridge.config")


@dataclass
class PhoneConfig:
    """Configuration for a single phone."""
    device_id: str              # Unique identifier (from mDNS service name)
    display_name: str           # Human-readable name ("Pixel 7", "Sachin's Phone")
    last_ip: str = ""           # Last known IP address
    last_port: int = 8273       # Last known port
    preferred_drive: str = ""   # Preferred drive letter (e.g., "E:")
    auto_mount: bool = True     # Auto-mount when discovered
    color: str = "#4CAF50"      # Color tag for UI (green default)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PhoneConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class AppConfig:
    """Global application configuration."""
    version: int = 1
    scan_on_startup: bool = True
    show_notifications: bool = True
    start_minimized: bool = False
    start_with_windows: bool = False
    default_port: int = 8273
    vfs_cache_mode: str = "full"           # rclone VFS cache mode
    vfs_cache_max_age: str = "1h"          # rclone VFS cache max age
    vfs_read_chunk_size: str = "32M"       # rclone VFS read chunk size
    phones: dict[str, PhoneConfig] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["phones"] = {k: v.to_dict() if isinstance(v, PhoneConfig) else v
                       for k, v in self.phones.items()}
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        phones_raw = data.pop("phones", {})
        phones = {}
        for k, v in phones_raw.items():
            if isinstance(v, dict):
                phones[k] = PhoneConfig.from_dict(v)
            elif isinstance(v, PhoneConfig):
                phones[k] = v
        # Filter only valid fields
        valid_fields = {k for k in cls.__dataclass_fields__ if k != "phones"}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(phones=phones, **filtered)


class ConfigManager:
    """Manages loading and saving PhoneBridge configuration."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or (get_app_data_dir() / "config.json")
        self.config = self._load()
        logger.info(f"Config loaded from {self.config_path}")
        logger.info(f"  Known phones: {len(self.config.phones)}")

    def _load(self) -> AppConfig:
        """Load config from disk, or create default."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return AppConfig.from_dict(data)
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(f"Config file corrupted, using defaults: {e}")
                return AppConfig()
        return AppConfig()

    def save(self):
        """Persist config to disk."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config.to_dict(), f, indent=2, ensure_ascii=False)
        logger.debug("Config saved")

    def get_phone(self, device_id: str) -> Optional[PhoneConfig]:
        """Get phone config by device ID."""
        return self.config.phones.get(device_id)

    def upsert_phone(self, phone: PhoneConfig):
        """Add or update a phone config."""
        self.config.phones[phone.device_id] = phone
        self.save()
        logger.info(f"Phone config updated: {phone.display_name} ({phone.device_id})")

    def remove_phone(self, device_id: str):
        """Remove a phone from config."""
        if device_id in self.config.phones:
            name = self.config.phones[device_id].display_name
            del self.config.phones[device_id]
            self.save()
            logger.info(f"Phone removed from config: {name}")

    def get_all_phones(self) -> dict[str, PhoneConfig]:
        """Get all known phone configs."""
        return dict(self.config.phones)

    def assign_drive_letter(self, device_id: str, drive_letter: str):
        """Assign a preferred drive letter to a phone."""
        if device_id in self.config.phones:
            self.config.phones[device_id].preferred_drive = drive_letter
            self.save()
