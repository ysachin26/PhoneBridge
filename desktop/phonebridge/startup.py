"""
PhoneBridge — Windows Startup Manager

Manages the Windows Registry Run key to start PhoneBridge
automatically when the user logs in.
"""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("phonebridge.startup")

# Registry key path for current user auto-start
_REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_VALUE_NAME = "PhoneBridge"


def _get_startup_command() -> str:
    """
    Get the command to run PhoneBridge on startup.
    
    Handles both frozen (PyInstaller/cx_Freeze) and regular Python execution.
    """
    if getattr(sys, "frozen", False):
        # Running as a compiled executable
        return f'"{sys.executable}"'
    else:
        # Running from Python — use pythonw to avoid console window
        python_dir = Path(sys.executable).parent
        pythonw = python_dir / "pythonw.exe"
        
        if pythonw.exists():
            return f'"{pythonw}" -m phonebridge'
        else:
            return f'"{sys.executable}" -m phonebridge'


def is_startup_enabled() -> bool:
    """Check if PhoneBridge is registered to start with Windows."""
    if sys.platform != "win32":
        return False
    
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _REG_VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning(f"Error checking startup registry key: {e}")
        return False


def enable_startup() -> bool:
    """
    Add PhoneBridge to Windows startup.
    
    Creates a registry key at HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
    that points to the current PhoneBridge executable.
    
    Returns:
        True if successful, False otherwise
    """
    if sys.platform != "win32":
        logger.warning("Startup management is only supported on Windows")
        return False
    
    try:
        import winreg
        command = _get_startup_command()
        
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _REG_VALUE_NAME, 0, winreg.REG_SZ, command)
        
        logger.info(f"✅ Startup enabled: {command}")
        return True
    except OSError as e:
        logger.error(f"Failed to enable startup: {e}")
        return False


def disable_startup() -> bool:
    """
    Remove PhoneBridge from Windows startup.
    
    Removes the registry key at HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
    
    Returns:
        True if successful (or already not registered), False on error
    """
    if sys.platform != "win32":
        logger.warning("Startup management is only supported on Windows")
        return False
    
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _REG_VALUE_NAME)
        
        logger.info("✅ Startup disabled — registry key removed")
        return True
    except FileNotFoundError:
        logger.info("Startup was already disabled")
        return True
    except OSError as e:
        logger.error(f"Failed to disable startup: {e}")
        return False


def toggle_startup() -> bool:
    """
    Toggle the startup state.
    
    Returns:
        The new state (True = enabled, False = disabled)
    """
    if is_startup_enabled():
        disable_startup()
        return False
    else:
        enable_startup()
        return True
