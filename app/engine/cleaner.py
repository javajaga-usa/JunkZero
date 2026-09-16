"""Safe and audited deletion engine with Recycle Bin integration."""
from __future__ import annotations
import os
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel
import send2trash

from app.engine.classifier import is_system_protected_path, format_size

AUDIT_LOG_FILE = Path("cleaner_audit.log")

# Setup dedicated cleaner logger
logger = logging.getLogger("DiskPurgeCleaner")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.FileHandler(AUDIT_LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)


class CleanResult(BaseModel):
    total_requested: int
    deleted_count: int
    failed_count: int
    freed_bytes: int
    freed_formatted: str
    mode: str  # "recycle_bin" or "permanent"
    errors: List[Dict[str, str]] = []


def delete_items(
    items: List[Dict[str, any]],
    permanent: bool = True
) -> CleanResult:
    """
    Delete a batch of files and directories permanently from disk (bypassing Recycle Bin).
    """
    deleted_count = 0
    failed_count = 0
    freed_bytes = 0
    errors: List[Dict[str, str]] = []
    mode_str = "permanent" if permanent else "recycle_bin"

    for item in items:
        path_str = item.get("path", "")
        size = item.get("size_bytes", 0)
        is_dir = item.get("is_directory", False)

        if not path_str or not os.path.exists(path_str):
            continue

        # Critical Guardrail: NEVER delete protected system files
        if is_system_protected_path(path_str):
            msg = "Deletion blocked: protected system path"
            errors.append({"path": path_str, "error": msg})
            logger.warning(f"Blocked attempt to delete system path: {path_str}")
            failed_count += 1
            continue

        # Prevent root path deletion (e.g. C:\ or D:\)
        norm_path = os.path.abspath(path_str)
        if norm_path == os.path.abspath(os.path.splitdrive(norm_path)[0] + "\\"):
            msg = "Deletion blocked: root drive path"
            errors.append({"path": path_str, "error": msg})
            failed_count += 1
            continue

        try:
            # Auto-calculate size if not passed
            if size == 0:
                try:
                    if os.path.isdir(norm_path):
                        for root, _, files in os.walk(norm_path):
                            for f in files:
                                fp = os.path.join(root, f)
                                if not os.path.islink(fp):
                                    size += os.path.getsize(fp)
                    elif os.path.isfile(norm_path):
                        size = os.path.getsize(norm_path)
                except Exception:
                    pass

            if permanent:
                # Permanent deletion direct from disk
                if os.path.isdir(norm_path):
                    shutil.rmtree(norm_path)
                else:
                    os.remove(norm_path)
                logger.info(f"PermanentDelete: {norm_path} ({format_size(size)})")
            else:
                # Optional Recycle Bin fallback
                send2trash.send2trash(norm_path)
                logger.info(f"RecycleBin: {norm_path} ({format_size(size)})")

            deleted_count += 1
            freed_bytes += size

        except Exception as ex:
            failed_count += 1
            err_msg = str(ex)
            errors.append({"path": path_str, "error": err_msg})
            logger.error(f"Failed to delete {norm_path}: {err_msg}")

    return CleanResult(
        total_requested=len(items),
        deleted_count=deleted_count,
        failed_count=failed_count,
        freed_bytes=freed_bytes,
        freed_formatted=format_size(freed_bytes),
        mode=mode_str,
        errors=errors
    )
