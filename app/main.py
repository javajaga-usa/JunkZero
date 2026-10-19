"""FastAPI server and application entry point with Desktop GUI and Web support."""
from __future__ import annotations
import asyncio
import json
import logging
import logging.handlers
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import weakref
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__
from app.config import HOLDING_DAYS, MAC_PACKAGE_EXTENSIONS, OLD_DOWNLOAD_DAYS, RISK_SAFE, ScanOptions
from app.engine import (
    changes, cleaner, deletecheck, filetypes, linux, linux_scheduler, locations, notify, osinfo, permissions,
    reclaim, recycle, safeguards, scheduler, shortcuts, smart, spacelog, storage,
)
from app.engine.ai_advisor import analyze_item
from app.engine.classifier import GarbageItem
from app.engine.cleaner import CleanResult, delete_items
from app.engine.exclusions import rule_too_broad
from app.engine.inspector import inspect_path_hierarchy, FolderHierarchyView
from app.engine.locations import downloads_folder, junk_locations
from app.engine.scanner import FastScanner, ScanStats, get_available_drives
from app.engine.space import largest_items

# The packaged windowed exe has no console: give logging and uvicorn somewhere to write
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("JunkZeroServer")

LOG_MAX_BYTES = 1024 * 1024  # One log file, before it is rotated
LOG_BACKUPS = 3


def log_file() -> Path:
    """JunkZero's own log, in the data folder (%APPDATA%\\JunkZero\\logs on Windows)."""
    return storage.data_dir() / "logs" / "junkzero.log"


def setup_logging() -> None:
    """Write the log to a rotating file, and log crashes in any thread there too: the windowed
    exe has no console, so otherwise nothing would ever show what went wrong."""
    path = log_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                                                       encoding="utf-8", delay=True)
    except OSError as e:
        logger.warning(f"Could not open the log file: {e}")
        return
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    if not any(getattr(h, "baseFilename", None) == handler.baseFilename for h in root.handlers):
        root.addHandler(handler)

    def log_crash(kind, value, tb, thread_name: str = "main") -> None:
        logger.error(f"Unexpected error in {thread_name}:\n" + "".join(traceback.format_exception(kind, value, tb)))

    previous_hook = sys.excepthook

    def excepthook(kind, value, tb):
        log_crash(kind, value, tb)
        previous_hook(kind, value, tb)

    previous_thread_hook = threading.excepthook

    def thread_hook(args):
        log_crash(args.exc_type, args.exc_value, args.exc_traceback, getattr(args.thread, "name", "a thread"))
        previous_thread_hook(args)

    sys.excepthook = excepthook
    threading.excepthook = thread_hook

# FastAPI App
app = FastAPI(title="JunkZero API", version=__version__)
# Only answer requests addressed to this machine, so a web page can't reach the API by
# pointing its own domain name at 127.0.0.1 (DNS rebinding)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

# A secret made fresh at every launch and handed only to the window JunkZero opens, in the
# part of the address after # (which is never sent to a server, so nothing can fetch it from
# here). Every API request must carry it, so neither another web page in the browser nor
# another program or user account on this computer can read the scan or delete files.
API_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-JunkZero-Token"
TOKEN_PARAM = "token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Says only which system this is and the app version
_OPEN_API_PATHS = {"/api/system/info"}
# EventSource can't send headers, so the live scan stream takes the token in its address
_TOKEN_IN_QUERY_PATHS = {"/api/scan/stream"}


def app_url(server_url: str) -> str:
    """The address JunkZero opens its window at, carrying this launch's token."""
    return f"{server_url}/#{TOKEN_PARAM}={API_TOKEN}"


def _token_ok(value: Optional[str]) -> bool:
    try:
        return secrets.compare_digest((value or "").encode("utf-8"), API_TOKEN.encode("utf-8"))
    except (TypeError, UnicodeError):
        return False


@app.middleware("http")
async def require_app_token(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and request.method not in _SAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Requests from other web pages are not allowed"}, status_code=403)
    if path.startswith("/api/") and path not in _OPEN_API_PATHS:
        token = request.headers.get(TOKEN_HEADER)
        if not token and path in _TOKEN_IN_QUERY_PATHS:
            token = request.query_params.get(TOKEN_PARAM)
        if not _token_ok(token):
            return JSONResponse({"detail": "Missing or wrong JunkZero app token"}, status_code=403)
    return await call_next(request)

# Global Scanner State
current_scanner: Optional[FastScanner] = None
scanner_lock = threading.Lock()
event_queue: asyncio.Queue = asyncio.Queue()
# Scanner -> set once its "completed" message is in its event queue; the scan stream ends only then
scan_done: "weakref.WeakKeyDictionary[Any, threading.Event]" = weakref.WeakKeyDictionary()


def _done_event(scanner: Any) -> Optional[threading.Event]:
    """The "completed was sent" flag of a scan started here (None for anything else)."""
    try:
        return scan_done.get(scanner) if scanner is not None else None
    except TypeError:
        return None


# How often the scan stream sends the scan's counts and step, even when nothing is found
PROGRESS_INTERVAL = 0.25


class ScanRequest(BaseModel):
    target_path: str = ""
    include_installers: bool = True
    include_java_builds: bool = True
    include_temp_junk: bool = True
    include_broken_downloads: bool = True
    include_stale_large: bool = True
    include_empty_folders: bool = True
    include_duplicates: bool = False
    include_old_downloads: bool = False
    include_custom_rules: bool = True
    include_leftovers: bool = False
    include_old_projects: bool = False
    # File type search: list files with these extensions ("mp4", ".mp4" or "*.mp4")
    include_file_types: bool = False
    file_types: List[str] = Field(default_factory=list, max_length=filetypes.MAX_FILE_TYPES)
    file_type_min_days: int = Field(default=0, ge=0, le=36500)
    # Look only for the chosen file types (every other category off)
    type_search_only: bool = False
    old_download_days: int = Field(default=OLD_DOWNLOAD_DAYS, ge=1, le=36500)
    scan_junk_locations: bool = False
    min_size_mb: float = Field(default=0.0, ge=0, le=10_000_000, allow_inf_nan=False)
    stale_days: int = Field(default=180, ge=1, le=36500)
    skip_system_dirs: bool = True


class CleanRequest(BaseModel):
    items: List[Dict[str, Any]]
    permanent: bool = False
    # "DELETE", when the confirm dialog asked the user to type it
    confirm_text: str = ""
    # The user chose "Unlock and delete" for items they had locked in Finder
    unlock: bool = False


class DeletePathsRequest(BaseModel):
    paths: List[str]
    permanent: bool = False
    confirm_text: str = ""
    unlock: bool = False


class DeletePreviewRequest(BaseModel):
    paths: List[str]
    # "scan": items from the scan results; "explorer": files and folders picked in Folder Explorer
    source: Literal["scan", "explorer"] = "scan"
    permanent: bool = False


class RestoreRequest(BaseModel):
    timestamp: float


class InspectPathRequest(BaseModel):
    path: str


class DeleteFolderRequest(BaseModel):
    path: str
    permanent: bool = False
    confirm_text: str = ""
    unlock: bool = False


class OpenFolderRequest(BaseModel):
    path: str


class AIAnalyzeRequest(BaseModel):
    path: str


class ExclusionsRequest(BaseModel):
    rules: List[str]


class AddExclusionRequest(BaseModel):
    rule: str = Field(min_length=1)


class JunkRulesRequest(BaseModel):
    rules: List[str]


class ScheduleRequest(BaseModel):
    frequency: str = "weekly"
    time: str = "09:00"
    day: str = "MON"
    paths: List[str] = Field(default_factory=list)
    include_junk_locations: bool = True


class LargestItemsRequest(BaseModel):
    path: str
    file_limit: int = Field(default=50, ge=1, le=500)
    folder_limit: int = Field(default=25, ge=1, le=200)


SCAN_OPTION_KEYS = (
    "include_installers", "include_java_builds", "include_temp_junk", "include_broken_downloads",
    "include_stale_large", "include_empty_folders", "include_duplicates", "include_old_downloads",
    "include_leftovers", "include_file_types", "include_old_projects",
)


class PreferencesRequest(BaseModel):
    theme: Literal["dark", "light"] = "dark"
    target_path: str = Field(default="", max_length=1024)
    scan_options: Dict[str, bool] = Field(default_factory=dict)
    file_types: List[str] = Field(default_factory=list, max_length=filetypes.MAX_FILE_TYPES)
    file_type_min_days: int = Field(default=0, ge=0, le=36500)


class EmptyRequest(BaseModel):
    older_than_days: int = Field(default=reclaim.EMPTY_AFTER_DAYS, ge=0, le=3650)


class NotifyRequest(BaseModel):
    enabled: bool = True
    min_mb: int = Field(default=notify.DEFAULT_MIN_MB, ge=0, le=notify.MAX_MIN_MB)


class ShortcutsRequest(BaseModel):
    places: List[str] = Field(default_factory=list)


class SeenRequest(BaseModel):
    generated_at: float


class ReportRequest(BaseModel):
    paths: List[str] = Field(default_factory=list)
    include_junk_locations: bool = True


@app.get("/api/system/drives")
def api_get_drives():
    """Return available drives (Windows drive letters or macOS disks) with disk usage statistics."""
    return {"drives": get_available_drives()}


@app.get("/api/system/info")
def api_system_info():
    """Which system this is, and what the UI calls its trash, file manager and scheduler."""
    return {**osinfo.platform_terms(), "version": __version__, "setup_warning": osinfo.setup_warning()}


@app.get("/api/system/cleanup-hints")
def api_cleanup_hints():
    """Junk only Windows itself (or an administrator) can remove, shown read-only with a pointer
    to the system's own cleanup tool."""
    hints = getattr(locations, "system_cleanup_hints", None)
    try:
        return {"hints": hints() if callable(hints) else []}
    except Exception as e:
        logger.warning(f"Could not list system cleanup hints: {e}")
        return {"hints": []}


RELEASES_API = "https://api.github.com/repos/javajaga-usa/JunkZero/releases/latest"
RELEASES_PAGE = "https://github.com/javajaga-usa/JunkZero/releases"
UPDATE_CHECK_TIMEOUT = 5


def _version_tuple(text: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", text or "")[:4])


@app.get("/api/update/check")
def api_update_check():
    """Ask GitHub for the newest release. Only when the user clicks "Check for updates"; JunkZero
    never does this on its own. Nothing is downloaded."""
    out = {"current": __version__, "latest": None, "newer": False, "url": RELEASES_PAGE, "error": ""}
    request = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"JunkZero/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=UPDATE_CHECK_TIMEOUT) as res:
            data = json.loads(res.read(1024 * 1024).decode("utf-8"))
        tag = str(data.get("tag_name") or "").strip()
        if not tag:
            raise ValueError("GitHub sent no version number")
        latest = tag[1:] if tag[:1] in ("v", "V") else tag
        out["latest"] = latest
        out["newer"] = _version_tuple(latest) > _version_tuple(__version__)
        page = data.get("html_url")
        if isinstance(page, str) and page.startswith("https://github.com/"):
            out["url"] = page
    except urllib.error.HTTPError as e:
        out["error"] = "No release has been published yet" if e.code == 404 else f"GitHub answered with error {e.code}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        out["error"] = f"Could not reach GitHub ({getattr(e, 'reason', e)}). Check the internet connection."
    except (ValueError, AttributeError) as e:
        out["error"] = f"GitHub's answer could not be read ({e})"
    return out


def _webview_version() -> str:
    try:
        from importlib.metadata import version
        return version("pywebview")
    except Exception:
        return "not installed"


def _webview2_version() -> str:
    """The Microsoft Edge WebView2 Runtime version pywebview draws the window with (Windows)."""
    if not osinfo.is_windows():
        return "not used on this system"
    try:
        import winreg
    except ImportError:
        return "unknown"
    key_path = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive, prefix in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\"),
                         (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\"), (winreg.HKEY_CURRENT_USER, "Software\\")):
        try:
            with winreg.OpenKey(hive, prefix + key_path) as key:
                value = str(winreg.QueryValueEx(key, "pv")[0])
                if value and value != "0.0.0.0":
                    return value
        except OSError:
            continue
    return "not found"


DIAGNOSTICS_LOG_LINES = 100


def _last_log_lines(count: int = DIAGNOSTICS_LOG_LINES) -> List[str]:
    try:
        with open(log_file(), "r", encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f.readlines()[-count:]]
    except OSError:
        return []


@app.get("/api/diagnostics")
def api_diagnostics():
    """Plain text to paste into a bug report: versions, system, data folder and the last log lines."""
    lines = [
        f"JunkZero {__version__}",
        f"System: {platform.platform()} ({platform.machine()})",
        f"OS version: {platform.version()}",
        f"Python: {platform.python_version()} ({platform.python_implementation()})",
        f"Packaged exe: {'yes' if getattr(sys, 'frozen', False) else 'no (running from source)'}",
        f"pywebview: {_webview_version()}",
        f"WebView2 Runtime: {_webview2_version()}",
        *([f"Linux desktop: {linux.desktop_name()}",
           f"Scheduler: {linux_scheduler.scheduler_label(linux_scheduler.scheduler_kind())}"] if osinfo.is_linux() else []),
        f"Data folder: {storage.data_dir()}",
        f"Log file: {log_file()}",
        "",
        f"Last {DIAGNOSTICS_LOG_LINES} log lines:",
    ]
    log_lines = _last_log_lines()
    lines += log_lines or ["(the log is empty)"]
    return {"text": "\n".join(lines)}


# AppleScript for the macOS folder picker; prints the chosen folder's POSIX path. "activate"
# brings the picker to the front: otherwise it opens behind the browser and looks like nothing
# happened. The folder to start in comes as an argument, never inside the script.
MAC_CHOOSE_FOLDER_SCRIPT = [
    "on run argv",
    "activate",
    'set promptText to "Select a drive or folder to scan for garbage files"',
    "if (count of argv) > 0 then",
    "return POSIX path of (choose folder with prompt promptText default location (POSIX file (item 1 of argv)))",
    "end if",
    "return POSIX path of (choose folder with prompt promptText)",
    "end run",
]
# osascript's error number when the user clicks Cancel
MAC_USER_CANCELLED = "-128"

# The JunkZero window (pywebview), when the app runs in its own window
gui_window = None
# The address JunkZero opened in the browser, when it runs there instead of in its own window
browser_url: Optional[str] = None


class BrowseRequest(BaseModel):
    # The folder the picker starts in (the current scan location)
    start: str = Field(default="", max_length=4096)


def mac_choose_folder_command(start: str = "") -> List[str]:
    command = ["osascript"]
    for line in MAC_CHOOSE_FOLDER_SCRIPT:
        command += ["-e", line]
    if start and os.path.isdir(start):
        command.append(start)
    return command


def _window_folder_dialog(start: str) -> Optional[str]:
    """Ask JunkZero's own window for a folder. None if there is no window or it can't."""
    window = gui_window
    if window is None:
        return None
    import webview
    dialog = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
    if dialog is None:
        dialog = getattr(webview, "FOLDER_DIALOG")
    chosen = window.create_file_dialog(dialog, directory=start if start and os.path.isdir(start) else "")
    if not chosen:
        return ""
    return chosen[0] if isinstance(chosen, (list, tuple)) else str(chosen)


def reveal_command(path: str, is_file: bool) -> List[str]:
    """Command that shows path in Finder (macOS) or Windows Explorer, selecting it if it's a file.
    (Linux tries more than one command: see linux.reveal.)"""
    if osinfo.is_macos():
        # "open" on a folder that is an app or package (Foo.app, a Photos library) would launch it
        return ["open", "-R", path] if is_file or path.lower().rstrip("/").endswith(MAC_PACKAGE_EXTENSIONS) else ["open", path]
    return ["explorer.exe", "/select,", path] if is_file else ["explorer.exe", path]


@app.post("/api/system/browse-folder")
def api_browse_folder(req: Optional[BrowseRequest] = None):
    """Open a native directory picker (Finder on macOS, Windows otherwise) and return the selected path.
    Returns {"path": ""} when the user cancels, plus "error" when the picker could not open."""
    start = (req.start if req else "").strip()
    # JunkZero's own window has a folder dialog of its own (no time limit, opens in front)
    try:
        window_choice = _window_folder_dialog(start)
    except Exception as e:
        logger.warning(f"Window folder picker failed, using the system picker: {e}")
        window_choice = None
    if window_choice is not None and not osinfo.is_macos():
        return {"path": window_choice} if window_choice and os.path.isdir(window_choice) else {"path": ""}
    if osinfo.is_linux():
        # zenity (Xfce, GNOME), kdialog (KDE Plasma) or yad, whichever is installed
        chosen, error = linux.choose_folder(start)
        if error:
            logger.warning(f"Native folder picker failed: {error}")
            return {"path": "", "error": error}
        return {"path": chosen}
    if osinfo.is_macos():
        chosen = window_choice
        if chosen is None:
            # If macOS won't start the picker in the current folder, open it without one
            attempts = [start, ""] if start and os.path.isdir(start) else [""]
            for attempt in attempts:
                try:
                    res = subprocess.run(mac_choose_folder_command(attempt), capture_output=True, text=True, timeout=300)
                except Exception as e:
                    logger.warning(f"Native folder picker failed: {e}")
                    return {"path": "", "error": f"The folder picker could not open ({e}). Type the folder path instead."}
                stderr = (getattr(res, "stderr", "") or "").strip()
                if not getattr(res, "returncode", 0):
                    break
                if MAC_USER_CANCELLED in stderr:
                    return {"path": ""}
                logger.warning(f"Native folder picker failed: {stderr}")
            else:
                return {"path": "", "error": f"The folder picker could not open ({stderr or 'unknown error'}). "
                                             "Type the folder path instead."}
            chosen = (res.stdout or "").strip()
        if len(chosen) > 1:
            chosen = chosen.rstrip("/")
        if chosen and os.path.isdir(chosen):
            return {"path": chosen}
        return {"path": ""}
    try:
        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$f.Description = 'Select a drive or folder to scan for garbage files';"
            "$f.ShowNewFolderButton = $false;"
            "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
        )
        # No time limit: the picker stays open for as long as the user needs
        res = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True, text=True,
        )
        selected_path = res.stdout.strip()
        if selected_path and os.path.exists(selected_path):
            return {"path": selected_path}
    except Exception as e:
        logger.warning(f"Native folder picker failed: {e}")
    return {"path": ""}


@app.post("/api/system/open-explorer")
def api_open_explorer(req: OpenFolderRequest):
    """Highlight the file or open its containing folder in Windows Explorer or Finder."""
    path = os.path.abspath(req.path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Path does not exist")

    try:
        if osinfo.is_linux():
            linux.reveal(path, os.path.isfile(path))
        else:
            subprocess.Popen(reveal_command(path, os.path.isfile(path)))
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scan/start")
async def api_start_scan(req: ScanRequest):
    """Start a multi-threaded scan for the given target path."""
    global current_scanner, event_queue

    if req.target_path or not req.scan_junk_locations:
        if not os.path.isdir(req.target_path):
            raise HTTPException(status_code=400, detail=f"Target path '{req.target_path}' is not a directory")

    try:
        file_types = filetypes.normalize_types(req.file_types)
    except filetypes.FileTypeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if req.type_search_only:
        if not req.target_path:
            raise HTTPException(status_code=400, detail="Pick a folder or drive to search")
        # Only the chosen types: every other category is off for this scan
        updates = {key: False for key in SCAN_OPTION_KEYS}
        updates.update(include_custom_rules=False, scan_junk_locations=False, include_file_types=True)
        req = req.model_copy(update=updates)
    if req.include_file_types and not file_types:
        raise HTTPException(status_code=400, detail="Pick at least one file type to search for")

    options = ScanOptions(
        target_path=req.target_path,
        include_installers=req.include_installers,
        include_java_builds=req.include_java_builds,
        include_temp_junk=req.include_temp_junk,
        include_broken_downloads=req.include_broken_downloads,
        include_stale_large=req.include_stale_large,
        include_empty_folders=req.include_empty_folders,
        include_duplicates=req.include_duplicates,
        include_old_downloads=req.include_old_downloads,
        old_download_days=req.old_download_days,
        downloads_dirs=[d for d in [downloads_folder()] if d],
        scan_junk_locations=req.scan_junk_locations,
        exclusions=storage.get_exclusions(),
        include_custom_rules=req.include_custom_rules,
        custom_rules=storage.get_custom_rules(),
        include_leftovers=req.include_leftovers,
        include_old_projects=req.include_old_projects,
        include_file_types=req.include_file_types,
        file_types=file_types,
        file_type_min_days=req.file_type_min_days,
        learning=smart.load_learning(),
        junk_locations=junk_locations(),
        min_file_size_bytes=int(req.min_size_mb * 1024 * 1024),
        stale_days=req.stale_days,
        skip_system_dirs=req.skip_system_dirs,
    )

    with scanner_lock:
        if current_scanner and current_scanner.stats.is_running:
            current_scanner.cancel()

        # Reset event queue
        event_queue = asyncio.Queue()
        current_scanner = FastScanner(options)
        scanner = current_scanner
        session_queue = event_queue
        scanner.stats.is_running = True
        done = threading.Event()
        scan_done[scanner] = done

    loop = asyncio.get_running_loop()

    def on_item_found(item: GarbageItem, stats: ScanStats):
        msg = {
            "type": "item_found",
            "item": item.model_dump(),
            "stats": {
                "files_scanned": stats.total_files_scanned,
                "dirs_scanned": stats.total_dirs_scanned,
                "garbage_count": stats.garbage_items_found,
                "garbage_bytes": stats.total_garbage_bytes,
                "category_counts": dict(stats.category_counts),
                "category_bytes": dict(stats.category_bytes),
            }
        }
        loop.call_soon_threadsafe(session_queue.put_nowait, msg)

    scanner.set_callback(on_item_found)
    type_search_only = req.type_search_only

    def finish(msg: Dict[str, Any]) -> None:
        # Runs on the event loop: the message is in the queue before the scan counts as done
        session_queue.put_nowait(msg)
        done.set()

    def run_worker():
        error = ""
        completion_msg: Dict[str, Any] = {"type": "completed", "stats": {}, "error": ""}
        try:
            try:
                scanner.run_scan()
            except Exception as e:
                logger.error("Scan failed:\n" + traceback.format_exc())
                error = f"The scan stopped because of an error: {e or type(e).__name__}"
            stats = scanner.stats
            stats.is_running = False
            error = error or str(getattr(stats, "error", "") or "")
            completion_msg = {
                "type": "completed",
                "stats": {
                    "files_scanned": stats.total_files_scanned,
                    "dirs_scanned": stats.total_dirs_scanned,
                    "garbage_count": stats.garbage_items_found,
                    "garbage_bytes": stats.total_garbage_bytes,
                    "category_counts": dict(stats.category_counts),
                    "category_bytes": dict(stats.category_bytes),
                    "elapsed_seconds": stats.elapsed_seconds,
                    "is_completed": stats.is_completed,
                    "is_cancelled": stats.is_cancelled,
                    "phase": getattr(stats, "phase", "done"),
                    "unreadable_dirs": stats.unreadable_dirs,
                    "privacy_blocked_dirs": stats.privacy_blocked_dirs,
                    "unreadable_examples": list(stats.unreadable_examples),
                    "privacy_fix": permissions.mac_privacy_fix() if stats.privacy_blocked_dirs else "",
                },
                "error": error,
            }
            # A file type search is not a cleanup scan: it doesn't count for "new since last
            # scan", and finding the same files again teaches the smart score nothing
            if stats.is_completed and not type_search_only and not error:
                try:
                    completion_msg["changes"] = changes.compare_and_remember(scanner.options, scanner.garbage_items)
                except Exception as e:
                    logger.warning(f"Could not compare with the previous scan: {e}")
        except Exception as e:
            logger.error("Scan worker failed:\n" + traceback.format_exc())
            completion_msg["error"] = error or f"The scan stopped because of an error: {e or type(e).__name__}"
        finally:
            # "completed" is always sent, error or not, so the page never waits forever
            try:
                loop.call_soon_threadsafe(finish, completion_msg)
            except RuntimeError:
                done.set()  # The event loop is gone: nobody is listening any more

    threading.Thread(target=run_worker, daemon=True).start()
    return {"status": "started", "target_path": req.target_path}


@app.get("/api/scan/stream")
async def api_scan_stream():
    """SSE endpoint streaming live scan items and statistics to the UI."""
    session_queue = event_queue
    session_scanner = current_scanner
    session_done = _done_event(session_scanner)

    async def sse_generator():
        last_progress = 0.0
        while True:
            try:
                # Wait for next event with a timeout
                msg = await asyncio.wait_for(session_queue.get(), timeout=PROGRESS_INTERVAL)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") == "completed":
                    break
            except asyncio.TimeoutError:
                if session_done is None:
                    # No scan started here (or results from a saved report): nothing more will come
                    with scanner_lock:
                        if (session_scanner is None or not session_scanner.stats.is_running) and session_queue.empty():
                            break
                elif session_done.is_set() and session_queue.empty():
                    break  # Its "completed" message went out on another stream
                if time.time() - last_progress >= 1.0:
                    yield ": heartbeat\n\n"
            # Counts and the current step about four times a second, even when nothing is found
            if session_done is not None and not session_done.is_set() and \
                    time.time() - last_progress >= PROGRESS_INTERVAL:
                last_progress = time.time()
                yield f"data: {json.dumps({'type': 'progress', 'stats': progress_stats(session_scanner.stats)})}\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


def progress_stats(stats: Any) -> Dict[str, Any]:
    """The live counts of a scan and the step it is on."""
    running = bool(getattr(stats, "is_running", False))
    start = getattr(stats, "start_time", 0) or 0
    elapsed = round(time.time() - start, 2) if running and start else getattr(stats, "elapsed_seconds", 0)
    return {
        "files_scanned": stats.total_files_scanned,
        "dirs_scanned": stats.total_dirs_scanned,
        "garbage_count": stats.garbage_items_found,
        "garbage_bytes": stats.total_garbage_bytes,
        "elapsed_seconds": elapsed,
        "phase": getattr(stats, "phase", "walking" if running else "done") or ("walking" if running else "done"),
    }


@app.post("/api/scan/stop")
def api_stop_scan():
    """Cancel currently running scan."""
    global current_scanner
    with scanner_lock:
        if current_scanner and current_scanner.stats.is_running:
            current_scanner.cancel()
            return {"status": "cancelled"}
    return {"status": "not_running"}


@app.get("/api/scan/status")
def api_scan_status():
    """Return status of the latest scanner session."""
    with scanner_lock:
        if not current_scanner:
            return {"status": "idle"}
        stats = current_scanner.stats
        done = _done_event(current_scanner)
        # Running until its "completed" message is out, so the page doesn't stop waiting early
        running = bool(stats.is_running) or (done is not None and not done.is_set())
        return {
            "status": "running" if running else "finished",
            "is_running": running,
            "phase": progress_stats(stats)["phase"],
            "error": str(getattr(stats, "error", "") or ""),
            "target_path": stats.target_path,
            "files_scanned": stats.total_files_scanned,
            "dirs_scanned": stats.total_dirs_scanned,
            "garbage_count": stats.garbage_items_found,
            "garbage_bytes": stats.total_garbage_bytes,
            "category_counts": dict(stats.category_counts),
            "category_bytes": dict(stats.category_bytes),
            "elapsed_seconds": stats.elapsed_seconds,
            "unreadable_dirs": stats.unreadable_dirs,
            "privacy_blocked_dirs": stats.privacy_blocked_dirs,
            "items_count": len(current_scanner.garbage_items),
        }


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def scan_results() -> Dict[str, Dict[str, Any]]:
    """Items JunkZero itself listed, by path: the latest scan in this window, or the latest
    scheduled report when nothing was scanned since JunkZero opened. Only these can be deleted
    through /api/clean, so an item a newer scan left out (excluded, changed) can't be."""
    found: Dict[str, Dict[str, Any]] = {}
    scanner = current_scanner
    if scanner is not None:
        for item in list(scanner.garbage_items):
            found[_key(item.path)] = item.model_dump()
        return found
    report = storage.load_report() or {}
    for item in report.get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            found[_key(item["path"])] = item
    return found


def _explorer_entries(paths: List[str]) -> List[Dict[str, Any]]:
    return [{"path": p, "is_directory": os.path.isdir(p), "size_bytes": 0} for p in paths if isinstance(p, str) and p]


def _scan_entries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Scan results for the delete summary. A folder the scan wasn't sure about (a "build" or
    "bin" folder, say) is looked inside, so personal files in it still ask for DELETE typed."""
    return [{**i, "look_inside": bool(i.get("is_directory")) and i.get("risk_level") != RISK_SAFE} for i in items]


def _check_confirmation(summary: Dict[str, Any], confirm_text: str) -> None:
    if not deletecheck.confirmed(summary, confirm_text):
        raise HTTPException(status_code=400, detail=f"Type {deletecheck.CONFIRM_WORD} to confirm this delete")


def _with_errors(result: CleanResult, errors: List[Dict[str, str]]) -> CleanResult:
    if errors:
        result.total_requested += len(errors)
        result.failed_count += len(errors)
        result.errors.extend(errors)
    return result


@app.post("/api/delete-preview")
def api_delete_preview(req: DeletePreviewRequest):
    """What a delete would remove: counts, top folders, file types, personal and cloud files,
    items on drives without a Recycle Bin, and whether the user must type DELETE."""
    cloud = safeguards.cloud_folders()
    if req.source == "scan":
        index = scan_results()
        entries = [index[_key(p)] for p in req.paths if isinstance(p, str) and _key(p) in index]
        return deletecheck.summarize(_scan_entries(entries), req.permanent, cloud)
    return deletecheck.summarize(_explorer_entries(req.paths), req.permanent, cloud, look_inside=True)


@app.post("/api/clean")
def api_clean_items(req: CleanRequest) -> CleanResult:
    """Safely delete the selected garbage items (Recycle Bin default or permanent).
    Only items from JunkZero's own scan results are accepted; what they are (category, size,
    other copies of a duplicate) comes from those results, not from the request."""
    if not req.items:
        raise HTTPException(status_code=400, detail="No items provided for deletion")

    index = scan_results()
    items: List[Dict[str, Any]] = []
    unknown: List[Dict[str, str]] = []
    for requested in req.items:
        path = requested.get("path") if isinstance(requested, dict) else None
        item = index.get(_key(path)) if isinstance(path, str) and path else None
        if item is None:
            unknown.append({"path": str(path), "error": "Not in the latest scan results; scan again first"})
        else:
            items.append(item)

    _check_confirmation(deletecheck.summarize(_scan_entries(items), req.permanent, safeguards.cloud_folders()),
                        req.confirm_text)
    result = _with_errors(delete_items(items, permanent=req.permanent, unlock=req.unlock), unknown)
    # Items a Cancel left untouched are the last ones: they were not deleted
    items = items[:len(items) - result.skipped_count]
    _record_history(result, [i["path"] for i in items], "scan results")
    try:
        smart.learn_deleted(items, [e.get("path", "") for e in result.errors])
    except OSError as e:
        logger.warning(f"Could not save what was learned from this cleanup: {e}")
    return result


@app.get("/api/clean/progress")
def api_clean_progress():
    """How far the delete running now has got: {"running", "done", "total"}."""
    progress = cleaner.delete_progress()
    return {"running": bool(progress["running"]), "done": int(progress["done"]), "total": int(progress["total"])}


@app.post("/api/clean/cancel")
def api_clean_cancel():
    """Stop the running delete after the batch it is on; the rest is left untouched."""
    return {"status": "cancelling" if cleaner.cancel_delete() else "not_running"}


@app.get("/api/learning")
def api_get_learning():
    """How much the smart score has learned from the user's past choices."""
    return smart.learning_summary()


@app.delete("/api/learning")
def api_forget_learning():
    smart.forget_learning()
    return smart.learning_summary()


def _record_history(result: CleanResult, paths: List[str], source: str) -> None:
    if result.deleted_count == 0:
        return
    try:
        storage.record_cleanup(result.model_dump(), paths, source)
    except OSError as e:
        logger.warning(f"Could not save cleanup history: {e}")
    # Free space after the cleanup, for the chart in Cleanup History
    spacelog.record_paths(paths[:50])


@app.get("/api/reclaim")
def api_reclaim(days: int = reclaim.EMPTY_AFTER_DAYS):
    """What JunkZero's cleanups still hold in the Recycle Bin / Trash and holding folders, and
    how much of it is from cleanups at least `days` old."""
    return reclaim.summary(max(0, min(3650, days)))


@app.post("/api/reclaim/empty")
def api_reclaim_empty(req: EmptyRequest):
    """Erase for good JunkZero's own items from cleanups at least older_than_days old. Nothing
    else in the Recycle Bin or Trash is touched; these items can't be restored afterwards."""
    if cleaner.delete_running():
        raise HTTPException(status_code=409, detail="Wait until the delete that is running finishes")
    result = reclaim.empty_old(req.older_than_days)
    if result["emptied"]:
        spacelog.record_paths([h for r in storage.load_history()[:20] for h in (r.get("paths") or [])[:5]])
    return {**result, "summary": reclaim.summary(req.older_than_days), "history": storage.load_history()}


@app.get("/api/space/history")
def api_space_history():
    """Free space at each scan and cleanup, per disk, and the cleanups (for the chart)."""
    return spacelog.history()


@app.post("/api/ai/analyze")
def api_ai_analyze(req: AIAnalyzeRequest):
    """Analyze a file or directory with intelligent heuristics / Gemini AI."""
    if not os.path.exists(req.path):
        raise HTTPException(status_code=404, detail="Target file/folder not found")
    result = analyze_item(req.path)
    return result.model_dump()


@app.post("/api/filesystem/folder-hierarchy")
def api_folder_hierarchy(req: InspectPathRequest) -> FolderHierarchyView:
    """Inspect a file or folder, return its parent folder hierarchy and directory contents."""
    if not os.path.exists(req.path):
        raise HTTPException(status_code=404, detail="Target path not found")
    return inspect_path_hierarchy(req.path)


@app.post("/api/filesystem/delete-folder")
def api_delete_folder(req: DeleteFolderRequest) -> CleanResult:
    """Delete a specified folder level (Recycle Bin by default, or permanently)."""
    if not os.path.exists(req.path):
        raise HTTPException(status_code=404, detail="Folder not found")
    if not os.path.isdir(req.path):
        raise HTTPException(status_code=400, detail="Path does not exist")
    _check_confirmation(deletecheck.summarize(
        _explorer_entries([req.path]), req.permanent, safeguards.cloud_folders(), look_inside=True), req.confirm_text)
    result = delete_items([{"path": req.path, "is_directory": True, "size_bytes": 0}], permanent=req.permanent,
                          unlock=req.unlock)
    _record_history(result, [req.path], "folder explorer")
    return result


@app.post("/api/filesystem/delete-paths")
def api_delete_paths(req: DeletePathsRequest) -> CleanResult:
    """Delete files and folders picked in Folder Explorer (Recycle Bin by default, or permanently)."""
    if not req.paths:
        raise HTTPException(status_code=400, detail="No items provided for deletion")
    entries = _explorer_entries(req.paths)
    _check_confirmation(deletecheck.summarize(
        entries, req.permanent, safeguards.cloud_folders(), look_inside=True), req.confirm_text)
    result = delete_items(entries, permanent=req.permanent, unlock=req.unlock)
    _record_history(result, [e["path"] for e in entries[:len(entries) - result.skipped_count]], "folder explorer")
    return result


@app.get("/api/system/junk-locations")
def api_junk_locations():
    """Known junk locations (temp, caches, crash dumps, logs) that exist for this user."""
    return {"locations": [loc.to_dict() for loc in junk_locations()]}


@app.post("/api/system/open-privacy-settings")
def api_open_privacy_settings():
    """Open System Settings > Privacy & Security > Full Disk Access (macOS), where the user can
    let JunkZero (or the Terminal that runs it) see and clean every folder and disk."""
    if not osinfo.is_macos():
        raise HTTPException(status_code=501, detail="Privacy settings only apply on a Mac")
    subprocess.Popen(["open", permissions.MAC_FULL_DISK_ACCESS_URL])
    return {"success": True, "app": permissions.privacy_app()}


@app.post("/api/system/open-recycle-bin")
def api_open_recycle_bin():
    """Open the Windows Recycle Bin (or the macOS Trash) so deleted items can be restored."""
    if osinfo.is_macos():
        subprocess.Popen(["open", os.path.expanduser("~/.Trash")])
        return {"success": True}
    if osinfo.is_linux():
        os.makedirs(linux.trash_folder(), exist_ok=True)
        subprocess.Popen(linux.open_trash_command(), env=linux.subprocess_env())
        return {"success": True}
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="The Recycle Bin can only be opened on Windows")
    subprocess.Popen(["explorer.exe", "shell:RecycleBinFolder"])
    return {"success": True}


@app.get("/api/exclusions")
def api_get_exclusions():
    return {"rules": storage.get_exclusions()}


@app.put("/api/exclusions")
def api_set_exclusions(req: ExclusionsRequest):
    return {"rules": storage.set_exclusions(req.rules)}


@app.post("/api/exclusions/add")
def api_add_exclusion(req: AddExclusionRequest):
    return {"rules": storage.set_exclusions(storage.get_exclusions() + [req.rule])}


def _check_junk_rules(rules: List[str]) -> None:
    broad = [r for r in rules if r.strip() and rule_too_broad(r)]
    if broad:
        raise HTTPException(status_code=400, detail=f"'{broad[0].strip()}' would flag everything. Use a narrower pattern such as render_*")


@app.get("/api/custom-rules")
def api_get_custom_rules():
    return {"rules": storage.get_custom_rules()}


@app.put("/api/custom-rules")
def api_set_custom_rules(req: JunkRulesRequest):
    _check_junk_rules(req.rules)
    return {"rules": storage.set_custom_rules(req.rules)}


@app.post("/api/custom-rules/add")
def api_add_custom_rule(req: AddExclusionRequest):
    _check_junk_rules([req.rule])
    return {"rules": storage.set_custom_rules(storage.get_custom_rules() + [req.rule])}


@app.get("/api/file-types/presets")
def api_file_type_presets():
    """Ready-made groups of file types for the file type search."""
    return {"presets": filetypes.presets(), "never_listed": filetypes.never_listed(),
            "max_types": filetypes.MAX_FILE_TYPES}


@app.get("/api/history")
def api_get_history():
    return {"history": storage.load_history()}


@app.post("/api/history/restore")
def api_restore_cleanup(req: RestoreRequest):
    """Put back the items of one cleanup from the Recycle Bin / Trash or JunkZero's holding folder."""
    record = storage.find_cleanup(req.timestamp)
    if record is None:
        raise HTTPException(status_code=404, detail="Cleanup not found in history")
    outcome = recycle.restore_cleanup(record)
    if outcome["restored"]:
        storage.mark_restored(req.timestamp, outcome["restored"])
    return {**outcome, "history": storage.load_history()}


@app.delete("/api/history")
def api_clear_history():
    storage.clear_history()
    return {"history": []}


@app.get("/api/schedule")
def api_get_schedule():
    return {
        "schedule": scheduler.get_schedule(),
        "supported": scheduler.scheduling_supported(),
        "last_report": scheduler.report_summary(storage.load_report()),
    }


@app.put("/api/schedule")
def api_set_schedule(req: ScheduleRequest):
    try:
        schedule = scheduler.save_schedule(req.frequency, req.time, req.day, req.paths, req.include_junk_locations)
    except scheduler.ScheduleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"schedule": schedule}


@app.delete("/api/schedule")
def api_delete_schedule():
    try:
        schedule = scheduler.delete_schedule()
    except scheduler.ScheduleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"schedule": schedule}


@app.post("/api/space/largest")
def api_largest_items(req: LargestItemsRequest):
    """The biggest files and folders under a path (read-only; nothing is deleted)."""
    if not os.path.isdir(req.path):
        raise HTTPException(status_code=400, detail=f"'{req.path}' is not a folder")
    try:
        return largest_items(req.path, req.file_limit, req.folder_limit, exclusions=storage.get_exclusions())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/system/disk-info")
def api_disk_info(path: str = ""):
    """The disk holding path, and what deleting from it does (Trash, holding folder or read-only)."""
    path = path.strip()
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Path not found")
    return {**recycle.disk_info(path), "holding_days": HOLDING_DAYS}


@app.get("/api/system/downloads-folder")
def api_downloads_folder():
    return {"path": downloads_folder()}


@app.get("/api/preferences")
def api_get_preferences():
    return {"preferences": storage.get_preferences()}


@app.put("/api/preferences")
def api_set_preferences(req: PreferencesRequest):
    prefs = req.model_dump()
    # Only known scan toggles are kept, so a stale or hand-edited file can't add options
    prefs["scan_options"] = {k: v for k, v in req.scan_options.items() if k in SCAN_OPTION_KEYS}
    # File type search settings are kept only when the page sends them
    if "file_types" in req.model_fields_set:
        try:
            prefs["file_types"] = filetypes.normalize_types(req.file_types)
        except filetypes.FileTypeError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        prefs.pop("file_types", None)
    if "file_type_min_days" not in req.model_fields_set:
        prefs.pop("file_type_min_days", None)
    return {"preferences": storage.set_preferences(prefs)}


@app.get("/api/notify")
def api_get_notify():
    return {"notify": notify.get_settings()}


@app.put("/api/notify")
def api_set_notify(req: NotifyRequest):
    return {"notify": notify.save_settings(req.enabled, req.min_mb)}


@app.get("/api/reports/unseen")
def api_unseen_report():
    """The latest scheduled report, when it found enough to tell about and hasn't been shown."""
    report = storage.load_report()
    unseen = notify.unseen_report(report)
    if unseen is not None:
        unseen["delete_bytes"] = notify.delete_bytes(report)
    return {"report": unseen}


@app.post("/api/reports/seen")
def api_report_seen(req: SeenRequest):
    notify.mark_seen(req.generated_at)
    return {"status": "ok"}


@app.get("/api/shortcuts")
def api_get_shortcuts():
    """Whether this copy has Start menu and desktop shortcuts, and whether to offer them."""
    return shortcuts.status()


@app.post("/api/shortcuts")
def api_add_shortcuts(req: ShortcutsRequest):
    try:
        return shortcuts.add(req.places)
    except shortcuts.ShortcutError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/shortcuts/dismiss")
def api_dismiss_shortcuts():
    return shortcuts.dismiss_offer()


@app.get("/api/reports/latest")
def api_latest_report():
    report = storage.load_report()
    if not report:
        raise HTTPException(status_code=404, detail="No scan report yet")
    return report



# Static UI Files Mounting
UI_DIR = Path(__file__).parent / "ui"
if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

    @app.get("/")
    def serve_index():
        # The page holds no secret: the app token reaches it only in the window's address
        html = (UI_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def _purge_holding() -> None:
    try:
        recycle.purge_holding()
    except OSError as e:
        logger.warning(f"Could not clear old items from the holding folder: {e}")


def pick_port(preferred: int = 8000) -> int:
    """The preferred port if it's free, otherwise any free port, so another program on 8000 isn't opened instead."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


# ---------------------------------------------------------------- One JunkZero at a time

INSTANCE_FILE = "instance.json"
_ERROR_ALREADY_EXISTS = 183
_instance_handle: Any = None  # The Windows mutex or the open lock file, held while JunkZero runs


def acquire_single_instance() -> bool:
    """True if this is the only JunkZero running for this user. Two windows deleting at once
    could each remove a different copy of the same duplicate."""
    global _instance_handle
    if osinfo.is_windows():
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.CreateMutexW(None, False, "Local\\JunkZero-single-instance")
            if handle and ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            _instance_handle = handle
            return True
        except (OSError, AttributeError):
            return True  # Can't tell: never stop JunkZero from opening
    try:
        f = open(storage.data_dir() / "instance.lock", "a+b")
    except OSError:
        return True
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        f.close()
        return True
    except OSError:
        f.close()
        return False
    _instance_handle = f
    return True


def remember_instance(port: int) -> None:
    try:
        storage.write_json(INSTANCE_FILE, {"port": port, "pid": os.getpid()})
    except OSError as e:
        logger.warning(f"Could not note this JunkZero's port: {e}")


def focus_running_instance(show_report: bool = False) -> bool:
    """Ask the JunkZero that is already open to bring its window to the front (and, after a
    notification was clicked, to show the latest scheduled report)."""
    info = storage.read_json(INSTANCE_FILE, {})
    port = info.get("port") if isinstance(info, dict) else None
    if not isinstance(port, int):
        return False
    query = "?report=1" if show_report else ""
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/instance/focus{query}", data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=3) as res:
            return json.loads(res.read().decode("utf-8")).get("focused") is True
    except (OSError, ValueError, AttributeError):
        return False


_OPEN_API_PATHS.add("/api/instance/focus")


@app.post("/api/instance/focus")
def api_focus_window(report: int = 0):
    """Another launch of JunkZero asks this one to show its window, and with report=1 (a clicked
    notification) to put the latest scheduled report in the table. It says nothing else."""
    window = gui_window
    if window is None:
        if browser_url:
            # JunkZero runs in the browser (no window of its own): open it there again
            webbrowser.open(browser_url)
            return {"focused": True}
        return {"focused": False}
    if report:
        try:
            window.evaluate_js("typeof loadLatestReport === 'function' && loadLatestReport()")
        except Exception:
            pass
    for step in ("restore", "show"):
        try:
            getattr(window, step)()
        except Exception:
            pass
    try:
        # Bring it in front of other windows without keeping it there
        window.on_top = True
        window.on_top = False
    except Exception:
        pass
    return {"focused": True}


def _tell_already_running() -> None:
    message = "JunkZero is already open. Use the JunkZero window that is already running."
    print(message, file=sys.stderr)
    if osinfo.is_windows():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "JunkZero", 0x40)
        except (OSError, AttributeError):
            pass


CLOSE_BLOCKED_MESSAGE = "JunkZero is still deleting. Wait until it finishes (or press Cancel) before closing."


def on_window_closing() -> bool:
    """The window's closing event: False keeps the window open while a delete runs, so it never
    stops halfway or carries on in a hidden process."""
    if not cleaner.delete_running():
        return True
    window = gui_window
    if window is not None:
        try:
            window.evaluate_js(f"typeof showToast === 'function' && showToast({json.dumps(CLOSE_BLOCKED_MESSAGE)}, 'error')")
        except Exception:
            pass
    return False


def _check_scheduled_task() -> None:
    try:
        if scheduler.ensure_task_current():
            logger.info("Pointed the scheduled scan at this copy of JunkZero")
    except Exception as e:
        logger.warning(f"Could not check the scheduled scan: {e}")


def _check_shortcuts() -> None:
    try:
        if shortcuts.repair():
            logger.info("Pointed JunkZero's shortcuts at this copy of JunkZero")
    except Exception as e:
        logger.warning(f"Could not check the shortcuts: {e}")


def _open_in_browser(url: str) -> None:
    global browser_url
    browser_url = url
    if not webbrowser.open(url):
        print(f"Open JunkZero in your web browser at {url}", flush=True)


def start_server(host: str = "127.0.0.1", port: int = 8000):
    """Start uvicorn server."""
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    """Main CLI entry point supporting both native Desktop GUI and Web mode."""
    import argparse
    parser = argparse.ArgumentParser(description="JunkZero - Intelligent Disk Cleaner")
    parser.add_argument("--version", action="version", version=f"JunkZero {__version__}")
    parser.add_argument("--port", type=int, default=None,
                        help="Port to bind server (default: 8000, or a free port if 8000 is taken)")
    parser.add_argument("--mode", choices=["gui", "browser", "server", "report"], default="gui",
                        help="Launch mode: 'gui' (Native Desktop Window), 'browser' (Browser UI), 'server' (API only), "
                             "or 'report' (scan and save a report without deleting anything)")
    parser.add_argument("--path", action="append", default=[],
                        help="Folder to scan in report mode (repeatable; defaults to the saved schedule)")
    parser.add_argument("--no-junk-locations", action="store_true",
                        help="In report mode, skip the junk locations (temp, caches, logs)")
    parser.add_argument("--show-report", action="store_true",
                        help="Open with the latest scheduled report in the table (used by notifications)")
    args = parser.parse_args()
    setup_logging()
    if osinfo.is_linux() and getattr(sys, "frozen", False):
        # Programs JunkZero starts (the browser, the file manager) must use the system's libraries
        linux.restore_library_path()

    if args.mode == "report":
        if args.path:
            report = scheduler.run_report(args.path, not args.no_junk_locations)
        else:
            scheduler.run_scheduled_report()
            report = storage.load_report() or {}
        print(f"Report saved: {report.get('item_count', 0)} items, "
              f"{report.get('total_bytes', 0)} bytes reclaimable. Nothing was deleted.")
        return

    if args.mode in ("gui", "browser") and not acquire_single_instance():
        if not focus_running_instance(args.show_report):
            _tell_already_running()
        sys.exit(0)

    # Items set aside on drives without a Recycle Bin are kept for a week
    threading.Thread(target=_purge_holding, daemon=True).start()
    # A schedule saved by a copy of JunkZero that was moved or downloaded again
    threading.Thread(target=_check_scheduled_task, daemon=True).start()
    # Start menu and desktop shortcuts JunkZero made for a copy that was moved since
    threading.Thread(target=_check_shortcuts, daemon=True).start()

    port = args.port if args.port is not None else pick_port(8000)
    remember_instance(port)
    server_url = f"http://127.0.0.1:{port}"
    window_url = app_url(server_url) + ("&report=latest" if args.show_report else "")

    if args.mode == "server":
        print(f"Open JunkZero at {window_url}", flush=True)
        start_server(port=port)
        return

    # Start FastAPI in background daemon thread
    server_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning"),
        daemon=True
    )
    server_thread.start()

    logger.info(f"JunkZero server started at {server_url}")

    if args.mode == "browser":
        # Launch default web browser
        _open_in_browser(window_url)
        try:
            while True:
                threading.Event().wait(1)
        except KeyboardInterrupt:
            logger.info("Exiting...")
            sys.exit(0)

    elif args.mode == "gui":
        try:
            import webview
            if hasattr(webview, "settings"):
                webview.settings["ALLOW_DOWNLOADS"] = True  # Lets "Export CSV" save files
            logger.info("Launching native Desktop GUI window...")
            global gui_window
            window = webview.create_window(
                title="JunkZero",
                url=window_url,
                width=1320,
                height=880,
                min_size=(980, 650),
                confirm_close=True
            )
            gui_window = window
            try:
                window.events.closing += on_window_closing
            except Exception as e:
                logger.warning(f"Could not watch the window closing: {e}")
            webview.start()
            sys.exit(0)
        except Exception as e:
            logger.warning(f"Could not open native WebView window ({e}), falling back to default browser.")
            _open_in_browser(window_url)
            try:
                while True:
                    threading.Event().wait(1)
            except KeyboardInterrupt:
                sys.exit(0)
    else:
        # Server mode
        try:
            while True:
                threading.Event().wait(1)
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()
