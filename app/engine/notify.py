"""Tell the user when a scheduled scan found something worth a look (or failed).

Scheduled scans only save a report. With notifications on, a scan that finds at least the
chosen amount shows a system notification: on Windows clicking it opens JunkZero with that
report in the table (through a "junkzero:" link registered for the current user only), on a
Mac and on Linux it says what was found. Whatever the system, the next time JunkZero opens it
offers the report it hasn't shown yet.
"""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from xml.sax.saxutils import escape, quoteattr

from app.engine import osinfo, storage
from app.engine.classifier import format_size

logger = logging.getLogger("JunkZeroNotify")

DEFAULT_MIN_MB = 500
MAX_MIN_MB = 10_000_000
PROTOCOL = "junkzero"
PROTOCOL_KEY = rf"Software\Classes\{PROTOCOL}"
REPO_ROOT = Path(__file__).resolve().parents[2]
# Windows only shows notifications from apps it knows. JunkZero isn't installed as a Store app,
# so its notifications are shown under Windows PowerShell's name, which every Windows has.
WINDOWS_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
# Shows the notification described in JUNKZERO_TOAST (text never goes into the script itself)
_TOAST_SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
    "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$x.LoadXml($env:JUNKZERO_TOAST);"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:JUNKZERO_APP_ID)"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)
_MAC_SCRIPT = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run"]


def get_settings() -> Dict[str, Any]:
    saved = storage.load_settings().get("notify")
    saved = saved if isinstance(saved, dict) else {}
    try:
        min_mb = int(saved.get("min_mb", DEFAULT_MIN_MB))
    except (TypeError, ValueError):
        min_mb = DEFAULT_MIN_MB
    return {
        "enabled": bool(saved.get("enabled", True)),
        "min_mb": max(0, min(MAX_MIN_MB, min_mb)),
        "seen_at": float(saved.get("seen_at") or 0),
        "clickable": osinfo.is_windows(),
    }


def save_settings(enabled: bool, min_mb: int) -> Dict[str, Any]:
    with storage.lock():
        current = get_settings()
        storage.update_settings(notify={"enabled": bool(enabled), "min_mb": max(0, min(MAX_MIN_MB, int(min_mb))),
                                        "seen_at": current["seen_at"]})
    if osinfo.is_windows():
        try:
            register_protocol() if enabled else unregister_protocol()
        except OSError as e:
            logger.warning(f"Could not set up the notification link: {e}")
    return get_settings()


def mark_seen(generated_at: float) -> None:
    with storage.lock():
        current = storage.load_settings().get("notify")
        current = dict(current) if isinstance(current, dict) else {}
        current["seen_at"] = max(float(current.get("seen_at") or 0), float(generated_at or 0))
        storage.update_settings(notify=current)


def _worth_telling(report: Optional[Dict[str, Any]], settings: Dict[str, Any]) -> bool:
    if not report or report.get("source") != "scheduled":
        return False
    return int(report.get("total_bytes") or 0) >= settings["min_mb"] * 1024 * 1024


def unseen_report(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The latest scheduled report, if it found enough to tell about and the app hasn't shown it."""
    settings = get_settings()
    if not settings["enabled"] or not _worth_telling(report, settings):
        return None
    if float(report.get("generated_at") or 0) <= settings["seen_at"]:
        return None
    return {k: v for k, v in report.items() if k != "items"}


def delete_bytes(report: Dict[str, Any]) -> int:
    """Bytes of the report's items marked Delete (the ones JunkZero is confident about)."""
    return sum(int(i.get("size_bytes") or 0) for i in report.get("items") or []
               if isinstance(i, dict) and i.get("recommendation") == "Delete")


def report_message(report: Dict[str, Any]) -> Tuple[str, str]:
    """(title, text) for a report worth telling about."""
    total = int(report.get("total_bytes") or 0)
    count = int(report.get("item_count") or 0)
    sure = delete_bytes(report)
    title = f"JunkZero found {format_size(total)} to clean"
    text = f"{count:,} item{'s' if count != 1 else ''} in your scheduled scan"
    text += f"; {format_size(sure)} of it is marked Delete." if sure else "; review them before deleting."
    return title, text + (" Click to review." if osinfo.is_windows() else " Open JunkZero to review.")


def failure_message(error: str) -> Tuple[str, str]:
    return "JunkZero's scheduled scan failed", (error or "Unknown error")[:200]


def after_report(report: Optional[Dict[str, Any]] = None, error: str = "") -> bool:
    """Called when a scheduled scan ends: notify if it found enough, or failed. Never raises."""
    try:
        settings = get_settings()
        if not settings["enabled"]:
            return False
        if error:
            return send(*failure_message(error), clickable=False)
        if not _worth_telling(report, settings):
            return False
        return send(*report_message(report))
    except Exception as e:  # A notification must never make the scheduled scan fail
        logger.warning(f"Could not show a notification: {e}")
        return False


def toast_xml(title: str, text: str, clickable: bool = True) -> str:
    launch = f' activationType="protocol" launch={quoteattr(PROTOCOL + ":report")}' if clickable else ""
    return (f"<toast{launch}><visual><binding template=\"ToastGeneric\">"
            f"<text>{escape(title)}</text><text>{escape(text)}</text>"
            "</binding></visual></toast>")


def send(title: str, text: str, clickable: bool = True) -> bool:
    """Show a system notification. False if this system has no way to."""
    if osinfo.is_windows():
        if clickable:
            try:
                register_protocol()
            except OSError as e:
                logger.warning(f"Could not set up the notification link: {e}")
                clickable = False
        env = {**os.environ, "JUNKZERO_TOAST": toast_xml(title, text, clickable), "JUNKZERO_APP_ID": WINDOWS_APP_ID}
        command = ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", _TOAST_SCRIPT]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60, creationflags=flags)
        return res.returncode == 0
    if osinfo.is_macos():
        command = ["osascript"]
        for line in _MAC_SCRIPT:
            command += ["-e", line]
        res = subprocess.run(command + [title, text], capture_output=True, text=True, timeout=30)
        return res.returncode == 0
    if shutil.which("notify-send"):
        res = subprocess.run(["notify-send", "--app-name=JunkZero", title, text], capture_output=True, timeout=30)
        return res.returncode == 0
    return False


# ---------------------------------------------------------------- Windows: the junkzero: link

def open_report_script() -> str:
    """Python source run by the junkzero: link when JunkZero runs from source."""
    return (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "sys.argv = ['JunkZero', '--show-report']\n"
        "from app.main import main\n"
        "main()\n"
    )


def protocol_command() -> str:
    """What Windows runs when the notification is clicked: JunkZero, showing the latest report."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --show-report'
    script = storage.data_dir() / "open_report.pyw"
    script.write_text(open_report_script(), encoding="utf-8")
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return f'"{windowless if windowless.exists() else exe}" "{script}"'


def register_protocol() -> None:
    """Register junkzero: for the current user (HKEY_CURRENT_USER, no administrator needed)."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:JunkZero report")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, protocol_command())


def unregister_protocol() -> None:
    import winreg
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, PROTOCOL_KEY + sub)
        except OSError:
            pass
