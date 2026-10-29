"""Validated configuration and local file storage helpers."""

import json
import ntpath
from pathlib import Path


DEFAULT_CONFIG = {
    "followed_dynamic_types": ["DYNAMIC_TYPE_AV", "DYNAMIC_TYPE_DRAW"],
    "check_interval_minutes": 1,
    "followed_mids": [],
    "close_behavior": "tray",
    "sound_enabled": True,
    "sound_mode": "system",
    "system_sound": "asterisk",
    "sound_files": [],
}
VALID_DYNAMIC_TYPES = frozenset({"DYNAMIC_TYPE_AV", "DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_FORWARD"})
VALID_SOUND_MODES = frozenset({"system", "wav"})
VALID_SYSTEM_SOUNDS = frozenset({"asterisk", "exclamation", "question", "hand"})
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 7 * 24 * 60


def normalise_sound_files(paths) -> list[str]:
    if not isinstance(paths, list):
        return []
    files = []
    seen = set()
    for path in paths:
        if not isinstance(path, str) or not path.strip():
            continue
        path = path.strip()
        key = ntpath.normcase(ntpath.normpath(path))
        if key not in seen:
            seen.add(key)
            files.append(path)
    return files


def normalise_config(config) -> dict:
    if not isinstance(config, dict):
        config = {}
    merged = DEFAULT_CONFIG.copy()
    merged.update(config)
    raw_types = merged.get("followed_dynamic_types")
    if not isinstance(raw_types, list):
        raw_types = DEFAULT_CONFIG["followed_dynamic_types"]
    types = []
    for item in raw_types:
        if isinstance(item, str) and item in VALID_DYNAMIC_TYPES and item not in types:
            types.append(item)
    merged["followed_dynamic_types"] = types or list(DEFAULT_CONFIG["followed_dynamic_types"])
    try:
        interval = int(merged.get("check_interval_minutes", 1))
    except (TypeError, ValueError):
        interval = 1
    merged["check_interval_minutes"] = min(MAX_INTERVAL_MINUTES, max(MIN_INTERVAL_MINUTES, interval))
    raw_mids = merged.get("followed_mids")
    if not isinstance(raw_mids, list):
        raw_mids = []
    mids = []
    for item in raw_mids:
        value = str(item).strip()
        if value.isdigit() and value not in mids:
            mids.append(value)
    merged["followed_mids"] = mids
    merged["close_behavior"] = merged.get("close_behavior") if merged.get("close_behavior") in {"tray", "exit"} else "tray"
    merged["sound_enabled"] = merged.get("sound_enabled") if isinstance(merged.get("sound_enabled"), bool) else True
    merged["sound_mode"] = merged.get("sound_mode") if merged.get("sound_mode") in VALID_SOUND_MODES else "system"
    merged["system_sound"] = merged.get("system_sound") if merged.get("system_sound") in VALID_SYSTEM_SOUNDS else "asterisk"
    raw_files = config.get("sound_files") if "sound_files" in config else [config.get("sound_file")]
    merged["sound_files"] = normalise_sound_files(raw_files)
    merged.pop("sound_file", None)
    return merged


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = DEFAULT_CONFIG.copy()
        save_config(path, config)
    except json.JSONDecodeError:
        config = DEFAULT_CONFIG.copy()
    except OSError as error:
        raise RuntimeError(f"读取配置失败：{error}") from error
    return normalise_config(config)


def save_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.with_suffix(".json.tmp")
    temp_file.write_text(json.dumps(normalise_config(config), ensure_ascii=False, indent=2), encoding="utf-8")
    temp_file.replace(path)
