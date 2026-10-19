"""Scheduled, report-only scans via Windows Task Scheduler, macOS launchd, or a systemd user timer or
cron on Linux (app.engine.linux_scheduler). Scheduled runs never delete anything."""
from __future__ import annotations
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from xml.sax.saxutils import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import ScanOptions
from app.engine import linux_scheduler, osinfo, smart, storage
from app.engine.locations import junk_locations
from app.engine.scanner import FastScanner

TASK_NAME = "JunkZero Scheduled Scan"
# macOS: the launchd job, saved as ~/Library/LaunchAgents/<label>.plist
LAUNCHD_LABEL = "com.junkzero.scheduled-scan"
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
        # The last scheduled run that failed, or the task that could not be set up: {"at", "error"}
        "last_error": schedule.get("last_error"),
    }


def validate_schedule(frequency: str, time_str: str, day: str, paths: List[str], include_junk_locations: bool) -> None:
    if frequency not in ("daily", "weekly"):
        raise ScheduleError("Frequency must be daily or weekly")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_str or ""):
        raise ScheduleError("Time must be HH:MM (24-hour)")
    if frequency == "weekly" and day not in WEEKDAYS:
        raise ScheduleError("Day must be one of " + ", ".join(WEEKDAYS))
    if not paths and not include_junk_locations:
        raise ScheduleError("Choose at least one folder or include the "
                            + ("Windows junk locations" if osinfo.is_windows() else "junk locations"))
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


def task_action(script_path: str) -> Tuple[str, str]:
    """(program, arguments) Task Scheduler runs. The packaged JunkZero.exe has no Python next
    to it, so it runs itself in report mode instead of the launcher script."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--mode report"
    return _pythonw(), f'"{script_path}"'


def task_command(script_path: str) -> str:
    program, arguments = task_action(script_path)
    return f'"{program}" {arguments}'


_XML_DAYS = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",
             "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"}


def task_xml(frequency: str, time_str: str, day: str, program: str, arguments: str) -> str:
    """The Task Scheduler task. Unlike schtasks' defaults it also runs on battery, and a run
    missed while the computer was off or asleep is made up as soon as it can be."""
    if frequency == "daily":
        when = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    else:
        when = (f"<ScheduleByWeek><DaysOfWeek><{_XML_DAYS[day]} /></DaysOfWeek>"
                "<WeeksInterval>1</WeeksInterval></ScheduleByWeek>")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>JunkZero report-only scan (never deletes anything)</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2020-01-01T{time_str}:00</StartBoundary>
      <Enabled>true</Enabled>
      {when}
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(program)}</Command><Arguments>{escape(arguments)}</Arguments></Exec>
  </Actions>
</Task>
"""


def schtasks_create_args(xml_path: str) -> List[str]:
    return ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/XML", xml_path]


def _task_scheduler() -> bool:
    """True on Windows, where schedules go to Task Scheduler."""
    return os.name == "nt"


def _run_schtasks(args: List[str]) -> subprocess.CompletedProcess:
    if not _task_scheduler():
        raise ScheduleError("Scheduled scans use Windows Task Scheduler and are only available on Windows")
    res = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise ScheduleError((res.stderr or res.stdout or "schtasks failed").strip())
    return res


def _create_windows_task(frequency: str, time_str: str, day: str, script_path: str) -> None:
    program, arguments = task_action(script_path)
    fd, xml_path = tempfile.mkstemp(prefix="junkzero-task-", suffix=".xml", dir=storage.data_dir())
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(task_xml(frequency, time_str, day, program, arguments))
        _run_schtasks(schtasks_create_args(xml_path))
    finally:
        try:
            os.remove(xml_path)
        except OSError:
            pass


def windows_task_command() -> Optional[str]:
    """The program the saved task runs, or None when there is no such task. Any failure counts
    as "not there", whatever language Windows words its message in."""
    try:
        res = _run_schtasks(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    except (ScheduleError, OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"<Command>(.*?)</Command>", getattr(res, "stdout", "") or "", re.S)
    if not m:
        return ""
    return m.group(1).strip().replace("&amp;", "&").replace("&quot;", '"').strip('"')


def scheduling_supported() -> bool:
    if osinfo.is_linux():
        return linux_scheduler.scheduler_kind() is not None
    return os.name == "nt" or osinfo.is_macos()


def launchd_program_args(script_path: str) -> List[str]:
    """What launchd runs: the packaged app in report mode, or Python with the launcher script."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--mode", "report"]
    return [sys.executable, script_path]


def launchd_plist(frequency: str, time_str: str, day: str, program_args: List[str]) -> bytes:
    """A launchd job that runs the report at the chosen time (launchd weekdays: 0 = Sunday)."""
    hour, minute = (int(x) for x in time_str.split(":"))
    when: Dict[str, int] = {"Hour": hour, "Minute": minute}
    if frequency == "weekly":
        when["Weekday"] = (WEEKDAYS.index(day) + 1) % 7
    return plistlib.dumps({
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": program_args,
        "StartCalendarInterval": when,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
    })


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)


def _install_launch_agent(plist: bytes) -> None:
    path = launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _launchctl("unload", str(path))  # Replace an existing schedule; fails harmlessly if none
    path.write_bytes(plist)
    res = _launchctl("load", "-w", str(path))
    if res.returncode != 0:
        raise ScheduleError((res.stderr or res.stdout or "launchctl failed").strip())


def _remove_launch_agent() -> None:
    path = launch_agent_path()
    if path.exists():
        _launchctl("unload", "-w", str(path))
        path.unlink()


def save_schedule(frequency: str, time_str: str, day: str, paths: List[str], include_junk_locations: bool) -> Dict[str, Any]:
    validate_schedule(frequency, time_str, day, paths, include_junk_locations)
    if osinfo.is_macos():
        script = storage.data_dir() / "scheduled_scan.py"
        script.write_text(launcher_script(), encoding="utf-8")
        _install_launch_agent(launchd_plist(frequency, time_str, day, launchd_program_args(str(script))))
    elif osinfo.is_linux():
        script = storage.data_dir() / "scheduled_scan.py"
        script.write_text(launcher_script(), encoding="utf-8")
        try:
            linux_scheduler.install(frequency, time_str, day, launchd_program_args(str(script)))
        except (linux_scheduler.LinuxScheduleError, OSError, subprocess.SubprocessError) as e:
            raise ScheduleError(str(e))
    else:
        script = storage.data_dir() / "scheduled_scan.pyw"
        script.write_text(launcher_script(), encoding="utf-8")
        _create_windows_task(frequency, time_str, day, str(script))
    previous = get_schedule()
    storage.update_settings(schedule={
        "enabled": True, "frequency": frequency, "time": time_str, "day": day,
        "paths": paths, "include_junk_locations": include_junk_locations,
        "last_run": previous.get("last_run"), "last_error": None,
    })
    return get_schedule()


def _same_program(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def ensure_task_current() -> bool:
    """At startup: if a schedule is on but its task is missing or runs another program (the exe
    was moved or downloaded again), set it up again for this copy of JunkZero. True if it was
    re-created. A failure is kept as last_error for the Schedule screen."""
    schedule = get_schedule()
    if not schedule["enabled"]:
        return False
    try:
        if osinfo.is_macos():
            script = storage.data_dir() / "scheduled_scan.py"
            wanted = launchd_program_args(str(script))
            try:
                current = plistlib.loads(launch_agent_path().read_bytes()).get("ProgramArguments")
            except (OSError, ValueError, plistlib.InvalidFileException):
                current = None
            if current == wanted and (getattr(sys, "frozen", False) or script.exists()):
                return False
            script.write_text(launcher_script(), encoding="utf-8")
            _install_launch_agent(launchd_plist(schedule["frequency"], schedule["time"], schedule["day"], wanted))
            return True
        if osinfo.is_linux():
            script = storage.data_dir() / "scheduled_scan.py"
            wanted = launchd_program_args(str(script))
            if linux_scheduler.current_command() == wanted and (getattr(sys, "frozen", False) or script.exists()):
                return False
            script.write_text(launcher_script(), encoding="utf-8")
            linux_scheduler.install(schedule["frequency"], schedule["time"], schedule["day"], wanted)
            return True
        if not _task_scheduler():
            return False
        script = storage.data_dir() / "scheduled_scan.pyw"
        program, _ = task_action(str(script))
        current = windows_task_command()
        if current and _same_program(current, program) and (getattr(sys, "frozen", False) or script.exists()):
            return False
        script.write_text(launcher_script(), encoding="utf-8")
        _create_windows_task(schedule["frequency"], schedule["time"], schedule["day"], str(script))
        return True
    except (ScheduleError, linux_scheduler.LinuxScheduleError, OSError, subprocess.SubprocessError) as e:
        _record_error(f"Could not set up the scheduled scan again: {e}")
        return False


def _record_error(message: Optional[str]) -> None:
    with storage.lock():
        current = storage.load_settings().get("schedule")
        if isinstance(current, dict):
            error = {"at": time.time(), "error": message} if message else None
            storage.update_settings(schedule={**current, "last_error": error})


def delete_schedule() -> Dict[str, Any]:
    if osinfo.is_macos():
        _remove_launch_agent()
    elif osinfo.is_linux():
        try:
            linux_scheduler.remove()
        except (linux_scheduler.LinuxScheduleError, OSError, subprocess.SubprocessError) as e:
            raise ScheduleError(str(e))
    else:
        try:
            _run_schtasks(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
        except ScheduleError:
            # Already removed in Task Scheduler: just record it as off. Whether it is still
            # there is asked, not read from the message (which is in Windows' own language).
            if windows_task_command() is not None:
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
        junk_locations=junk_locations(),
        scan_junk_locations=include_junk_locations,
        learning=smart.load_learning(),
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
    """Entry point for Task Scheduler, launchd, systemd timers and cron."""
    from app.engine import notify
    schedule = get_schedule()
    try:
        report = run_report(schedule["paths"], schedule["include_junk_locations"], source="scheduled")
    except Exception as e:
        # Kept for the Schedule screen, so a run that keeps failing doesn't go unnoticed
        message = f"The scheduled scan failed: {e}" if str(e) else f"The scheduled scan failed ({type(e).__name__})"
        _record_error(message)
        notify.after_report(error=message)
        return 1
    # A system notification when it found enough to be worth a look (never fails the run)
    notify.after_report(report)
    # The scan can take minutes: only stamp the run, so changes made in the app meanwhile stay
    with storage.lock():
        current = storage.load_settings().get("schedule")
        if isinstance(current, dict):
            storage.update_settings(schedule={**current, "last_run": time.time(), "last_error": None})
    return 0


def report_summary(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not report:
        return None
    return {k: v for k, v in report.items() if k != "items"}
