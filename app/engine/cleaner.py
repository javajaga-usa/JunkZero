"""Safe and audited deletion engine with Recycle Bin integration."""
from __future__ import annotations
import os
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from pydantic import BaseModel, Field
import send2trash

from app.config import CAT_EMPTY_FOLDERS, VCS_DIR_NAMES
from app.engine.classifier import is_system_protected_path, format_size
from app.engine import storage
from app.engine.locations import is_protected_user_folder, protected_user_folders

# Kept with the rest of JunkZero's data (%APPDATA%\JunkZero), not in whatever folder the app started from
AUDIT_LOG_FILE = storage.data_dir() / "cleaner_audit.log"

# Setup dedicated cleaner logger
logger = logging.getLogger("JunkZeroCleaner")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.FileHandler(AUDIT_LOG_FILE, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)


def folder_has_files(dir_path: str) -> bool:
    """Return True if the folder contains anything other than (empty) subfolders, at any depth."""
    for root, dirs, files in os.walk(dir_path, onerror=_raise):
        if files:
            return True
        for d in dirs:
            if os.path.islink(os.path.join(root, d)):
                return True
    return False


def in_vcs_folder(path: str) -> bool:
    """True if the path is a .git/.svn/.hg/.bzr folder or lies inside one."""
    parts = os.path.normpath(path).replace("\\", "/").lower().split("/")
    return any(part in VCS_DIR_NAMES for part in parts)


def _raise(err: OSError) -> None:
    raise err


class CleanResult(BaseModel):
    total_requested: int
    deleted_count: int
    failed_count: int
    freed_bytes: int
    freed_formatted: str
    mode: str  # "recycle_bin" or "permanent"
    errors: List[Dict[str, str]] = Field(default_factory=list)


def delete_items(
    items: List[Dict[str, Any]],
    permanent: bool = False
) -> CleanResult:
    """
    Delete a batch of files and directories. Items go to the Recycle Bin by default;
    pass permanent=True to erase them from disk directly.
    """
    deleted_count = 0
    failed_count = 0
    freed_bytes = 0
    errors: List[Dict[str, str]] = []
    mode_str = "permanent" if permanent else "recycle_bin"
    user_folders = protected_user_folders()

    for item in items:
        path_str = item.get("path", "")
        size = item.get("size_bytes", 0)

        if not isinstance(path_str, str) or not path_str or "\0" in path_str:
            errors.append({"path": str(path_str), "error": "Invalid or empty path"})
            failed_count += 1
            continue
        if not isinstance(size, int) or size < 0:
            errors.append({"path": path_str, "error": "Invalid size_bytes"})
            failed_count += 1
            continue

        # Critical Guardrail: NEVER delete protected system files
        if is_system_protected_path(path_str) or is_system_protected_path(os.path.realpath(path_str)):
            msg = "Deletion blocked: protected system path"
            errors.append({"path": path_str, "error": msg})
            logger.warning(f"Blocked attempt to delete system path: {path_str}")
            failed_count += 1
            continue

        # The user profile and its main personal folders (Documents, Desktop...) are never removed whole
        if is_protected_user_folder(path_str, folders=user_folders) or \
                is_protected_user_folder(os.path.realpath(path_str), folders=user_folders):
            msg = "Deletion blocked: personal folder (delete what's inside it instead)"
            errors.append({"path": path_str, "error": msg})
            logger.warning(f"Blocked attempt to delete personal folder: {path_str}")
            failed_count += 1
            continue

        # Version-control folders (.git, .svn...) and anything inside them are never deleted
        if in_vcs_folder(path_str):
            msg = "Deletion blocked: version-control folder (.git, .svn, .hg)"
            errors.append({"path": path_str, "error": msg})
            failed_count += 1
            continue

        # Prevent root path deletion (e.g. C:\ or D:\)
        norm_path = os.path.abspath(path_str)
        resolved_path = Path(norm_path).resolve()
        if resolved_path == Path(resolved_path.anchor):
            msg = "Deletion blocked: root drive path"
            errors.append({"path": path_str, "error": msg})
            failed_count += 1
            continue

        if not path_str or not os.path.lexists(norm_path):
            errors.append({"path": path_str, "error": "Path does not exist"})
            failed_count += 1
            continue
        if os.path.islink(norm_path) or getattr(os.path, "isjunction", lambda _: False)(norm_path):
            errors.append({"path": path_str, "error": "Deletion blocked: symbolic link or junction"})
            failed_count += 1
            continue

        # A folder flagged as empty may have gained files since the scan
        if item.get("category") == CAT_EMPTY_FOLDERS:
            try:
                still_empty = os.path.isdir(norm_path) and not folder_has_files(norm_path)
            except OSError:
                still_empty = False
            if not still_empty:
                msg = "Deletion skipped: folder is no longer empty"
                errors.append({"path": path_str, "error": msg})
                logger.warning(f"Skipped empty-folder delete, folder now has content: {norm_path}")
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
                # Default: move to the Recycle Bin so the user can restore it
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
