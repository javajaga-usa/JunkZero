"""Scheduled, report-only scans via Windows Task Scheduler. Scheduled runs never delete anything."""
from __future__ import annotations
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import ScanOptions
from app.engine import storage
from app.engine.locations import windows_junk_locations
from app.engine.scanner import FastScanner

TASK_NAME = "JunkZero Scheduled Scan"
REPORT_ITEMS_LIMIT = 5000
WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
REPO_ROOT = Path(__file__).resolve().parents[2]


class ScheduleError(Exception):
    pass


def get_schedule() -> Dict[str, Any]:
    schedule = storage.load_settings().get("schedule") or {}
    return {
        "enabled": bool(schedule.get("enabled")),
        "frequency": schedule.get("frequency", "weekly"),
        "time": schedule.get("time", "09:00"),
        "day": schedule.get("day", "MON"),
        "paths": schedule.get("paths", []),
        "include_junk_locations": bool(schedule.get("include_junk_locations", True)),
        "last_run": schedule.get("last_run"),
    }


def validate_schedule(frequency: str, time_str: str, day: str, paths: List[str], include_junk_locations: bool) -> None:
    if frequency not in ("daily", "weekly"):
        raise ScheduleError("Frequency must be daily or weekly")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_str or ""):
        raise ScheduleError("Time must be HH:MM (24-hour)")
    if frequency == "weekly" and day not in WEEKDAYS:
        raise ScheduleError("Day must be one of " + ", ".join(WEEKDAYS))
    if not paths and not include_junk_locations:
        raise ScheduleError("Choose at least one folder or include the Windows junk locations")
    for p in paths:
        if not os.path.isdir(p):
            raise ScheduleError(f"Folder not found: {p}")


def launcher_script() -> str:
    """Python source run by Task Scheduler; reads the saved schedule at run time."""
    return (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from app.engine.scheduler import run_scheduled_report\n"
        "sys.exit(run_scheduled_report())\n"
    )


def _pythonw() -> str:
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else exe)


def task_command(script_path: str) -> str:
    """Command Task Scheduler runs. The packaged JunkZero.exe has no Python next to it,
    so it runs itself in report mode instead of the launcher script."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --mode report'
    return f'"{_pythonw()}" "{script_path}"'


def schtasks_create_args(frequency: str, time_str: str, day: str, command: str) -> List[str]:
    args = ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/TR", command, "/ST", time_str]
    if frequency == "daily":
        args += ["/SC", "DAILY"]
    else:
        args += ["/SC", "WEEKLY", "/D", day]
    return args


def _run_schtasks(args: List[str]) -> None:
    if os.name != "nt":
        raise ScheduleError("Scheduled scans use Windows Task Scheduler and are only available on Windows")
    res = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise ScheduleError((res.stderr or res.stdout or "schtasks failed").strip())


def save_schedule(frequency: str, time_str: str, day: str, paths: List[str], include_junk_locations: bool) -> Dict[str, Any]:
    validate_schedule(frequency, time_str, day, paths, include_junk_locations)
    script = storage.data_dir() / "scheduled_scan.pyw"
    script.write_text(launcher_script(), encoding="utf-8")
    _run_schtasks(schtasks_create_args(frequency, time_str, day, task_command(str(script))))
    previous = get_schedule()
    storage.update_settings(schedule={
        "enabled": True, "frequency": frequency, "time": time_str, "day": day,
        "paths": paths, "include_junk_locations": include_junk_locations,
        "last_run": previous.get("last_run"),
    })
    return get_schedule()


def delete_schedule() -> Dict[str, Any]:
    try:
        _run_schtasks(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
    except ScheduleError as e:
        # Already removed in Task Scheduler: just record it as off
        if "cannot find" not in str(e).lower():
            raise
    schedule = get_schedule()
    schedule["enabled"] = False
    storage.update_settings(schedule=schedule)
    return get_schedule()


def run_report(paths: List[str], include_junk_locations: bool = True, source: str = "manual") -> Dict[str, Any]:
    """Scan and save a report. Nothing is deleted."""
    existing = [p for p in paths if os.path.isdir(p)]
    options = ScanOptions(
        target_path=existing[0] if existing else "",
        extra_paths=existing[1:],
        exclusions=storage.get_exclusions(),
        custom_rules=storage.get_custom_rules(),
        junk_locations=windows_junk_locations(),
        scan_junk_locations=include_junk_locations,
    )
    scanner = FastScanner(options)
    items = scanner.run_scan()
    items.sort(key=lambda i: i.size_bytes, reverse=True)
    report = {
        "generated_at": time.time(),
        "source": source,
        "paths": existing,
        "include_junk_locations": include_junk_locations,
        "item_count": len(items),
        "total_bytes": sum(i.size_bytes for i in items),
        "category_counts": dict(scanner.stats.category_counts),
        "category_bytes": dict(scanner.stats.category_bytes),
        "elapsed_seconds": scanner.stats.elapsed_seconds,
        "items": [i.model_dump() for i in items[:REPORT_ITEMS_LIMIT]],
        "items_truncated": max(0, len(items) - REPORT_ITEMS_LIMIT),
    }
    storage.save_report(report)
    return report


def run_scheduled_report() -> int:
    """Entry point for Task Scheduler."""
    schedule = get_schedule()
    run_report(schedule["paths"], schedule["include_junk_locations"], source="scheduled")
    schedule["last_run"] = time.time()
    storage.update_settings(schedule=schedule)
    return 0


def report_summary(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not report:
        return None
    return {k: v for k, v in report.items() if k != "items"}
