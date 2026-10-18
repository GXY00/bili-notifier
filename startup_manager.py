"""Windows current-user startup registration for the desktop application."""

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "BilibiliNotifier"


def build_startup_command() -> str:
    """Build a command that starts this installation with the startup flag."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --startup'
    python_path = Path(sys.executable)
    if python_path.name.lower() == "python.exe":
        pythonw = python_path.with_name("pythonw.exe")
        if pythonw.exists():
            python_path = pythonw
    script = Path(__file__).with_name("bilibili_followed_dynamics.py").resolve()
    return f'"{python_path.resolve()}" "{script}" --startup'


def is_startup_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except (FileNotFoundError, OSError):
        return False


def set_startup_enabled(enabled: bool) -> None:
    if os.name != "nt":
        if enabled:
            raise RuntimeError("开机自启仅支持 Windows")
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, build_startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
