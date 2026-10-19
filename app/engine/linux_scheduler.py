"""Scheduled scans on Linux: a systemd user timer where systemd runs the desktop session, else
the user's crontab (MX Linux starts with SysVinit by default, where cron is the scheduler)."""
from __future__ import annotations
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from app.engine.linux import subprocess_env

SYSTEMD, CRON = "systemd", "cron"
UNIT_NAME = "junkzero-scan"
CRON_MARKER = "# JunkZero scheduled scan"
WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_SYSTEMD_DAYS = {"MON": "Mon", "TUE": "Tue", "WED": "Wed", "THU": "Thu", "FRI": "Fri", "SAT": "Sat", "SUN": "Sun"}


class LinuxScheduleError(Exception):
    pass


def _run(args: List[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=30, env=subprocess_env(), **kwargs)


def systemd_user_available() -> bool:
    """True if this session's systemd user manager answers (the computer booted with systemd and
    the user is logged in to a session that has one)."""
    if not shutil.which("systemctl") or not os.path.isdir("/run/systemd/system"):
        return False
    try:
        return _run(["systemctl", "--user", "show-environment"]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def cron_available() -> bool:
    return bool(shutil.which("crontab"))


def scheduler_kind() -> Optional[str]:
    """SYSTEMD, CRON, or None when neither can be used."""
    if systemd_user_available():
        return SYSTEMD
    if cron_available():
        return CRON
    return None


def scheduler_label(kind: Optional[str]) -> str:
    return {SYSTEMD: "a systemd user timer", CRON: "cron"}.get(kind or "", "systemd or cron")


# ---------------------------------------------------------------- systemd

def unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "systemd" / "user"


def _systemd_quote(arg: str) -> str:
    """One ExecStart argument: systemd expands % specifiers and $VARIABLES, so they are doubled."""
    arg = arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{arg}"'


def systemd_service(program_args: List[str]) -> str:
    return (
        "[Unit]\n"
        "Description=JunkZero report-only scan (never deletes anything)\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={' '.join(_systemd_quote(a) for a in program_args)}\n"
        "Nice=10\n"
        "IOSchedulingClass=idle\n"
    )


def systemd_timer(frequency: str, time_str: str, day: str) -> str:
    """Persistent=true makes up a run missed while the computer was off or asleep."""
    when = f"*-*-* {time_str}:00" if frequency == "daily" else f"{_SYSTEMD_DAYS[day]} *-*-* {time_str}:00"
    return (
        "[Unit]\n"
        "Description=JunkZero scheduled scan\n\n"
        "[Timer]\n"
        f"OnCalendar={when}\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def parse_exec_start(service_text: str) -> Optional[List[str]]:
    m = re.search(r"^ExecStart=(.*)$", service_text, re.M)
    if not m:
        return None
    try:
        args = shlex.split(m.group(1))
    except ValueError:
        return None
    return [a.replace("%%", "%").replace("$$", "$") for a in args]


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return _run(["systemctl", "--user", *args])


def install_systemd(frequency: str, time_str: str, day: str, program_args: List[str]) -> None:
    folder = unit_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{UNIT_NAME}.service").write_text(systemd_service(program_args), encoding="utf-8")
    (folder / f"{UNIT_NAME}.timer").write_text(systemd_timer(frequency, time_str, day), encoding="utf-8")
    _systemctl("daemon-reload")
    res = _systemctl("enable", "--now", f"{UNIT_NAME}.timer")
    if res.returncode != 0:
        raise LinuxScheduleError((res.stderr or res.stdout or "systemctl failed").strip())
    # A timer that was already running keeps its old time until it is restarted
    _systemctl("restart", f"{UNIT_NAME}.timer")


def remove_systemd() -> None:
    folder = unit_dir()
    timer, service = folder / f"{UNIT_NAME}.timer", folder / f"{UNIT_NAME}.service"
    if not timer.exists() and not service.exists():
        return
    if shutil.which("systemctl"):
        try:
            _systemctl("disable", "--now", f"{UNIT_NAME}.timer")
        except (OSError, subprocess.SubprocessError):
            pass
    for path in (timer, service):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    if shutil.which("systemctl"):
        try:
            _systemctl("daemon-reload")
        except (OSError, subprocess.SubprocessError):
            pass


def systemd_command() -> Optional[List[str]]:
    try:
        text = (unit_dir() / f"{UNIT_NAME}.service").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not (unit_dir() / f"{UNIT_NAME}.timer").exists():
        return None
    return parse_exec_start(text)


# ---------------------------------------------------------------- cron

def cron_line(frequency: str, time_str: str, day: str, program_args: List[str]) -> str:
    """One crontab line (cron weekdays: 0 = Sunday). % ends a cron command, so it is escaped."""
    hour, minute = (int(x) for x in time_str.split(":"))
    weekday = "*" if frequency == "daily" else str((WEEKDAYS.index(day) + 1) % 7)
    command = " ".join(shlex.quote(a) for a in program_args).replace("%", "\\%")
    return f"{minute} {hour} * * {weekday} {command} {CRON_MARKER}"


def read_crontab() -> str:
    """The user's crontab ("" when there is none)."""
    try:
        res = _run(["crontab", "-l"])
    except (OSError, subprocess.SubprocessError) as e:
        raise LinuxScheduleError(f"Could not read the crontab: {e}")
    if res.returncode != 0:
        if "no crontab" in (res.stderr or "").lower():
            return ""
        raise LinuxScheduleError((res.stderr or "crontab -l failed").strip())
    return res.stdout or ""


def without_junkzero(crontab: str) -> List[str]:
    return [line for line in crontab.splitlines() if not line.rstrip().endswith(CRON_MARKER)]


def write_crontab(lines: List[str]) -> None:
    text = "\n".join(lines).strip("\n")
    try:
        res = _run(["crontab", "-"], input=(text + "\n") if text else "")
    except (OSError, subprocess.SubprocessError) as e:
        raise LinuxScheduleError(f"Could not save the crontab: {e}")
    if res.returncode != 0:
        raise LinuxScheduleError((res.stderr or "crontab failed").strip())


def install_cron(frequency: str, time_str: str, day: str, program_args: List[str]) -> None:
    lines = without_junkzero(read_crontab())
    write_crontab(lines + [cron_line(frequency, time_str, day, program_args)])


def remove_cron() -> None:
    if not cron_available():
        return
    current = read_crontab()
    lines = without_junkzero(current)
    if lines != current.splitlines():
        write_crontab(lines)


def cron_command() -> Optional[List[str]]:
    if not cron_available():
        return None
    try:
        crontab = read_crontab()
    except LinuxScheduleError:
        return None
    for line in crontab.splitlines():
        if line.rstrip().endswith(CRON_MARKER):
            fields = line.rstrip()[:-len(CRON_MARKER)].split(None, 5)
            if len(fields) < 6:
                return []
            try:
                return [a.replace("\\%", "%") for a in shlex.split(fields[5])]
            except ValueError:
                return []
    return None


# ---------------------------------------------------------------- Both

def install(frequency: str, time_str: str, day: str, program_args: List[str]) -> str:
    """Set up the schedule with systemd or cron (and remove it from the other). Returns which."""
    kind = scheduler_kind()
    if kind is None:
        raise LinuxScheduleError("Scheduled scans need systemd or cron, and neither was found. "
                                 "Install cron in a terminal: sudo apt install cron")
    if kind == SYSTEMD:
        install_systemd(frequency, time_str, day, program_args)
        remove_cron()
    else:
        install_cron(frequency, time_str, day, program_args)
        remove_systemd()
    return kind


def remove() -> None:
    remove_systemd()
    remove_cron()


def current_command() -> Optional[List[str]]:
    """The program the saved schedule runs, from the systemd timer or the crontab, or None."""
    return systemd_command() or cron_command()
