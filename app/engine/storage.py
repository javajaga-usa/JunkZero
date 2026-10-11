"""Small JSON-backed persistence for user settings, cleanup history and scan reports."""
from __future__ import annotations
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

HISTORY_LIMIT = 200          # Cleanup records kept
HISTORY_PATHS_LIMIT = 500    # Paths stored per cleanup record
SCAN_MEMORY_LIMIT = 20       # Scan targets remembered for "new since last scan"
SCAN_MEMORY_PATHS_LIMIT = 50000  # Flagged paths remembered per target

_lock = threading.Lock()


def data_dir() -> Path:
    """Per-user folder for JunkZero state (override with JUNKZERO_DATA_DIR)."""
    override = os.environ.get("JUNKZERO_DATA_DIR")
    if override:
        base = Path(override)
    elif os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / "JunkZero"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "JunkZero"
    else:
        base = Path.home() / ".junkzero"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _read_json(name: str, default: Any) -> Any:
    path = data_dir() / name
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_json(name: str, value: Any) -> None:
    path = data_dir() / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
    os.replace(tmp, path)


def lock() -> threading.Lock:
    """The lock guarding JunkZero's JSON files (for read-modify-write in other modules)."""
    return _lock


read_json = _read_json
write_json = _write_json


# ---------------------------------------------------------------- Settings

def load_settings() -> Dict[str, Any]:
    settings = _read_json("settings.json", {})
    return settings if isinstance(settings, dict) else {}


def update_settings(**changes: Any) -> Dict[str, Any]:
    with _lock:
        settings = load_settings()
        settings.update(changes)
        _write_json("settings.json", settings)
        return settings


def get_exclusions() -> List[str]:
    rules = load_settings().get("exclusions", [])
    return [r for r in rules if isinstance(r, str) and r.strip()]


def _clean_rules(rules: List[str]) -> List[str]:
    cleaned: List[str] = []
    for rule in rules:
        rule = rule.strip()
        if rule and rule not in cleaned:
            cleaned.append(rule)
    return cleaned


def set_exclusions(rules: List[str]) -> List[str]:
    cleaned = _clean_rules(rules)
    update_settings(exclusions=cleaned)
    return cleaned


def get_custom_rules() -> List[str]:
    rules = load_settings().get("custom_rules", [])
    return [r for r in rules if isinstance(r, str) and r.strip()]


def set_custom_rules(rules: List[str]) -> List[str]:
    cleaned = _clean_rules(rules)
    update_settings(custom_rules=cleaned)
    return cleaned


# ---------------------------------------------------------------- History

def load_history() -> List[Dict[str, Any]]:
    history = _read_json("history.json", [])
    return history if isinstance(history, list) else []


def record_cleanup(result: Dict[str, Any], paths: List[str], source: str) -> Dict[str, Any]:
    """Append one cleanup run to the history (newest first) and return the record."""
    deleted = [p for p in paths if p not in {e.get("path") for e in result.get("errors", [])}]
    record = {
        "timestamp": time.time(),
        "source": source,
        "mode": result.get("mode", "recycle_bin"),
        "deleted_count": result.get("deleted_count", 0),
        "failed_count": result.get("failed_count", 0),
        "freed_bytes": result.get("freed_bytes", 0),
        "paths": deleted[:HISTORY_PATHS_LIMIT],
        "paths_truncated": max(0, len(deleted) - HISTORY_PATHS_LIMIT),
        "started_at": result.get("started_at") or time.time(),
        # Items set aside on drives without a Recycle Bin (original -> holding folder path)
        "held": {p: d for p, d in (result.get("held") or {}).items() if p in deleted[:HISTORY_PATHS_LIMIT]},
        "restored": [],
    }
    with _lock:
        history = load_history()
        history.insert(0, record)
        _write_json("history.json", history[:HISTORY_LIMIT])
    return record


def find_cleanup(timestamp: float) -> Optional[Dict[str, Any]]:
    for record in load_history():
        if record.get("timestamp") == timestamp:
            return record
    return None


def mark_restored(timestamp: float, paths: List[str]) -> Optional[Dict[str, Any]]:
    """Remember which items of a cleanup were put back, and return the updated record."""
    with _lock:
        history = load_history()
        for record in history:
            if record.get("timestamp") == timestamp:
                record["restored"] = sorted(set(record.get("restored") or []) | set(paths))
                _write_json("history.json", history)
                return record
    return None


def clear_history() -> None:
    with _lock:
        _write_json("history.json", [])


# ---------------------------------------------------------------- Scan reports

def save_report(report: Dict[str, Any]) -> None:
    with _lock:
        _write_json("latest_report.json", report)


def load_report() -> Dict[str, Any] | None:
    report = _read_json("latest_report.json", None)
    return report if isinstance(report, dict) else None


# ---------------------------------------------------------------- Previous scans

def last_scan(key: str) -> Optional[Dict[str, Any]]:
    """What the previous completed scan of this target found: {"at", "paths", "categories"}."""
    memory = _read_json("scan_memory.json", {})
    entry = memory.get(key) if isinstance(memory, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("paths"), list):
        return None
    return entry


def remember_scan(key: str, paths: Iterable[str], categories: Iterable[str]) -> None:
    """Store the flagged paths of a completed scan; only the most recent targets are kept."""
    paths = list(paths)
    entry = {
        "at": time.time(),
        "paths": paths[:SCAN_MEMORY_PATHS_LIMIT],
        # A cut-off list can't tell new items from old ones, so it is not compared against
        "complete": len(paths) <= SCAN_MEMORY_PATHS_LIMIT,
        "categories": sorted(set(categories)),
    }
    with _lock:
        memory = _read_json("scan_memory.json", {})
        if not isinstance(memory, dict):
            memory = {}
        memory.pop(key, None)
        memory[key] = entry  # Re-inserted last, so dict order is oldest first
        while len(memory) > SCAN_MEMORY_LIMIT:
            memory.pop(next(iter(memory)))
        _write_json("scan_memory.json", memory)


# ---------------------------------------------------------------- UI preferences

def get_preferences() -> Dict[str, Any]:
    prefs = load_settings().get("preferences", {})
    return prefs if isinstance(prefs, dict) else {}


def set_preferences(prefs: Dict[str, Any]) -> Dict[str, Any]:
    update_settings(preferences=prefs)
    return prefs
