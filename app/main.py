"""FastAPI server and application entry point with Desktop GUI and Web support."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import ScanOptions
from app.engine.ai_advisor import analyze_item
from app.engine.classifier import GarbageItem
from app.engine.cleaner import CleanResult, delete_items
from app.engine.inspector import inspect_path_hierarchy, FolderHierarchyView
from app.engine.scanner import FastScanner, ScanStats, get_available_drives

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("JunkZeroServer")

# FastAPI App
app = FastAPI(title="JunkZero API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Scanner State
current_scanner: Optional[FastScanner] = None
scanner_lock = threading.Lock()
event_queue: asyncio.Queue = asyncio.Queue()


class ScanRequest(BaseModel):
    target_path: str
    include_installers: bool = True
    include_java_builds: bool = True
    include_temp_junk: bool = True
    include_broken_downloads: bool = True
    include_stale_large: bool = True
    min_size_mb: float = 0.0
    stale_days: int = 180
    skip_system_dirs: bool = True


class CleanRequest(BaseModel):
    items: List[Dict[str, Any]]
    permanent: bool = True


class InspectPathRequest(BaseModel):
    path: str


class DeleteFolderRequest(BaseModel):
    path: str
    permanent: bool = True


class OpenFolderRequest(BaseModel):
    path: str


class AIAnalyzeRequest(BaseModel):
    path: str


@app.get("/api/system/drives")
def api_get_drives():
    """Return available Windows drives with disk usage statistics."""
    return {"drives": get_available_drives()}


@app.post("/api/system/browse-folder")
def api_browse_folder():
    """Open a native Windows directory picker and return the selected path."""
    try:
        ps_cmd = (
            'powershell -WindowStyle Hidden -Command "'
            '[System.Reflection.Assembly]::LoadWithPartialName(\'System.windows.forms\') | Out-Null;'
            '$f = New-Object System.Windows.Forms.FolderBrowserDialog;'
            '$f.Description = \'Select a drive or folder to scan for garbage files\';'
            '$f.ShowNewFolderButton = $false;'
            'if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }'
            '"'
        )
        res = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=30)
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
            subprocess.Popen(f'explorer.exe /select,"{path}"')
        else:
            subprocess.Popen(f'explorer.exe "{path}"')
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scan/start")
async def api_start_scan(req: ScanRequest):
    """Start a multi-threaded scan for the given target path."""
    global current_scanner, event_queue

    if not os.path.exists(req.target_path):
        raise HTTPException(status_code=400, detail=f"Target path '{req.target_path}' does not exist")

    options = ScanOptions(
        target_path=req.target_path,
        include_installers=req.include_installers,
        include_java_builds=req.include_java_builds,
        include_temp_junk=req.include_temp_junk,
        include_broken_downloads=req.include_broken_downloads,
        include_stale_large=req.include_stale_large,
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
                "category_counts": stats.category_counts,
                "category_bytes": stats.category_bytes,
            }
        }
        loop.call_soon_threadsafe(event_queue.put_nowait, msg)

    current_scanner.set_callback(on_item_found)

    def run_worker():
        scanner = current_scanner
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
            loop.call_soon_threadsafe(event_queue.put_nowait, completion_msg)

    threading.Thread(target=run_worker, daemon=True).start()
    return {"status": "started", "target_path": req.target_path}


@app.get("/api/scan/stream")
async def api_scan_stream():
    """SSE endpoint streaming live scan items and statistics to the UI."""
    async def sse_generator():
        global event_queue
        while True:
            try:
                # Wait for next event with a timeout
                msg = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") == "completed":
                    break
            except asyncio.TimeoutError:
                # Heartbeat ping
                yield f": heartbeat\n\n"
                with scanner_lock:
                    if current_scanner and not current_scanner.stats.is_running and event_queue.empty():
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
            "category_counts": stats.category_counts,
            "category_bytes": stats.category_bytes,
            "elapsed_seconds": stats.elapsed_seconds,
            "items_count": len(current_scanner.garbage_items),
        }


@app.post("/api/clean")
def api_clean_items(req: CleanRequest) -> CleanResult:
    """Safely delete the selected garbage items (Recycle Bin default or permanent)."""
    if not req.items:
        raise HTTPException(status_code=400, detail="No items provided for deletion")

    result = delete_items(req.items, permanent=req.permanent)
    return result


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
    """Permanently delete a specified folder level."""
    if not os.path.exists(req.path):
        raise HTTPException(status_code=404, detail="Folder not found")
    if not os.path.isdir(req.path):
        raise HTTPException(status_code=400, detail="Path is not a directory")
    return delete_items([{"path": req.path, "is_directory": True, "size_bytes": 0}], permanent=req.permanent)



# Static UI Files Mounting
UI_DIR = Path(__file__).parent / "ui"
if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(str(UI_DIR / "index.html"))


def start_server(host: str = "127.0.0.1", port: int = 8000):
    """Start uvicorn server."""
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    """Main CLI entry point supporting both native Desktop GUI and Web mode."""
    import argparse
    parser = argparse.ArgumentParser(description="JunkZero - Intelligent Disk Cleaner")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind server (default: 8000)")
    parser.add_argument("--mode", choices=["gui", "browser", "server"], default="gui",
                        help="Launch mode: 'gui' (Native Desktop Window), 'browser' (Browser UI), or 'server' (API only)")
    args = parser.parse_args()

    port = args.port
    server_url = f"http://127.0.0.1:{port}"

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
            logger.info("Launching native Desktop GUI window...")
            window = webview.create_window(
                title="JunkZero - Intelligent Disk Garbage Detector & Cleaner",
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
