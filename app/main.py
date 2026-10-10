"""FastAPI server and application entry point with Desktop GUI and Web support."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__
from app.config import OLD_DOWNLOAD_DAYS, ScanOptions
from app.engine import changes, scheduler, storage
from app.engine.ai_advisor import analyze_item
from app.engine.classifier import GarbageItem
from app.engine.cleaner import CleanResult, delete_items
from app.engine.exclusions import rule_too_broad
from app.engine.inspector import inspect_path_hierarchy, FolderHierarchyView
from app.engine.locations import downloads_folder, windows_junk_locations
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

# FastAPI App
app = FastAPI(title="JunkZero API", version=__version__)
# Only answer requests addressed to this machine, so a web page can't reach the API by
# pointing its own domain name at 127.0.0.1 (DNS rebinding)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

# Global Scanner State
current_scanner: Optional[FastScanner] = None
scanner_lock = threading.Lock()
event_queue: asyncio.Queue = asyncio.Queue()


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
    old_download_days: int = Field(default=OLD_DOWNLOAD_DAYS, ge=1)
    scan_junk_locations: bool = False
    min_size_mb: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    stale_days: int = Field(default=180, ge=1)
    skip_system_dirs: bool = True


class CleanRequest(BaseModel):
    items: List[Dict[str, Any]]
    permanent: bool = False


class InspectPathRequest(BaseModel):
    path: str


class DeleteFolderRequest(BaseModel):
    path: str
    permanent: bool = False


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
)


class PreferencesRequest(BaseModel):
    theme: Literal["dark", "light"] = "dark"
    target_path: str = Field(default="", max_length=1024)
    scan_options: Dict[str, bool] = Field(default_factory=dict)


class ReportRequest(BaseModel):
    paths: List[str] = Field(default_factory=list)
    include_junk_locations: bool = True


@app.get("/api/system/drives")
def api_get_drives():
    """Return available Windows drives with disk usage statistics."""
    return {"drives": get_available_drives()}


@app.post("/api/system/browse-folder")
def api_browse_folder():
    """Open a native Windows directory picker and return the selected path."""
    try:
        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$f.Description = 'Select a drive or folder to scan for garbage files';"
            "$f.ShowNewFolderButton = $false;"
            "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True, text=True, timeout=30,
        )
        selected_path = res.stdout.strip()
        if selected_path and os.path.exists(selected_path):
            return {"path": selected_path}
    except Exception as e:
        logger.warning(f"Native folder picker failed: {e}")
    return {"path": ""}


@app.post("/api/system/open-explorer")
def api_open_explorer(req: OpenFolderRequest):
    """Highlight the file or open its containing folder in Windows Explorer."""
    path = os.path.abspath(req.path)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Path does not exist")

    try:
        if os.path.isfile(path):
            subprocess.Popen(['explorer.exe', '/select,', path])
        else:
            subprocess.Popen(['explorer.exe', path])
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
        junk_locations=windows_junk_locations(),
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

    def run_worker():
        if scanner:
            scanner.run_scan()
            completion_msg = {
                "type": "completed",
                "stats": {
                    "files_scanned": scanner.stats.total_files_scanned,
                    "dirs_scanned": scanner.stats.total_dirs_scanned,
                    "garbage_count": scanner.stats.garbage_items_found,
                    "garbage_bytes": scanner.stats.total_garbage_bytes,
                    "category_counts": scanner.stats.category_counts,
                    "category_bytes": scanner.stats.category_bytes,
                    "elapsed_seconds": scanner.stats.elapsed_seconds,
                    "is_completed": scanner.stats.is_completed,
                    "is_cancelled": scanner.stats.is_cancelled,
                }
            }
            if scanner.stats.is_completed:
                try:
                    completion_msg["changes"] = changes.compare_and_remember(scanner.options, scanner.garbage_items)
                except OSError as e:
                    logger.warning(f"Could not compare with the previous scan: {e}")
            loop.call_soon_threadsafe(session_queue.put_nowait, completion_msg)

    threading.Thread(target=run_worker, daemon=True).start()
    return {"status": "started", "target_path": req.target_path}


@app.get("/api/scan/stream")
async def api_scan_stream():
    """SSE endpoint streaming live scan items and statistics to the UI."""
    session_queue = event_queue
    session_scanner = current_scanner

    async def sse_generator():
        while True:
            try:
                # Wait for next event with a timeout
                msg = await asyncio.wait_for(session_queue.get(), timeout=1.0)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") == "completed":
                    break
            except asyncio.TimeoutError:
                # Heartbeat ping
                yield f": heartbeat\n\n"
                with scanner_lock:
                    if (session_scanner is None or not session_scanner.stats.is_running) and session_queue.empty():
                        break

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


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
        return {
            "status": "running" if stats.is_running else "finished",
            "target_path": stats.target_path,
            "files_scanned": stats.total_files_scanned,
            "dirs_scanned": stats.total_dirs_scanned,
            "garbage_count": stats.garbage_items_found,
            "garbage_bytes": stats.total_garbage_bytes,
            "category_counts": dict(stats.category_counts),
            "category_bytes": dict(stats.category_bytes),
            "elapsed_seconds": stats.elapsed_seconds,
            "items_count": len(current_scanner.garbage_items),
        }


@app.post("/api/clean")
def api_clean_items(req: CleanRequest) -> CleanResult:
    """Safely delete the selected garbage items (Recycle Bin default or permanent)."""
    if not req.items:
        raise HTTPException(status_code=400, detail="No items provided for deletion")

    result = delete_items(req.items, permanent=req.permanent)
    _record_history(result, [str(i.get("path", "")) for i in req.items], "scan results")
    return result


def _record_history(result: CleanResult, paths: List[str], source: str) -> None:
    if result.deleted_count == 0:
        return
    try:
        storage.record_cleanup(result.model_dump(), paths, source)
    except OSError as e:
        logger.warning(f"Could not save cleanup history: {e}")


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
    result = delete_items([{"path": req.path, "is_directory": True, "size_bytes": 0}], permanent=req.permanent)
    _record_history(result, [req.path], "folder explorer")
    return result


@app.get("/api/system/junk-locations")
def api_junk_locations():
    """Known Windows junk locations (temp, caches, crash dumps) that exist for this user."""
    return {"locations": [loc.to_dict() for loc in windows_junk_locations()]}


@app.post("/api/system/open-recycle-bin")
def api_open_recycle_bin():
    """Open the Windows Recycle Bin so deleted items can be restored."""
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


@app.get("/api/history")
def api_get_history():
    return {"history": storage.load_history()}


@app.delete("/api/history")
def api_clear_history():
    storage.clear_history()
    return {"history": []}


@app.get("/api/schedule")
def api_get_schedule():
    return {
        "schedule": scheduler.get_schedule(),
        "supported": os.name == "nt",
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
    return {"preferences": storage.set_preferences(prefs)}


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
        return FileResponse(str(UI_DIR / "index.html"))


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
                        help="In report mode, skip the Windows junk locations")
    args = parser.parse_args()

    if args.mode == "report":
        if args.path:
            report = scheduler.run_report(args.path, not args.no_junk_locations)
        else:
            scheduler.run_scheduled_report()
            report = storage.load_report() or {}
        print(f"Report saved: {report.get('item_count', 0)} items, "
              f"{report.get('total_bytes', 0)} bytes reclaimable. Nothing was deleted.")
        return

    port = args.port if args.port is not None else pick_port(8000)
    server_url = f"http://127.0.0.1:{port}"

    if args.mode == "server":
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
        webbrowser.open(server_url)
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
            window = webview.create_window(
                title="JunkZero",
                url=server_url,
                width=1320,
                height=880,
                min_size=(980, 650),
                confirm_close=True
            )
            webview.start()
            sys.exit(0)
        except Exception as e:
            logger.warning(f"Could not open native WebView window ({e}), falling back to default browser.")
            webbrowser.open(server_url)
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
