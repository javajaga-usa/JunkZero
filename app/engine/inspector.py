"""Hierarchy and Folder Inspector for interactive file-tree exploration and multi-level deletion."""
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel

from app.config import ScanOptions
from app.engine.classifier import classify_item, format_size, is_system_protected_path


class BreadcrumbItem(BaseModel):
    name: str
    path: str
    is_current: bool = False
    is_deletable: bool = True


class FolderEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size_bytes: int
    size_formatted: str
    modified_date: str
    is_target_file: bool = False
    is_garbage: bool = False
    garbage_category: Optional[str] = None
    risk_level: Optional[str] = None
    is_deletable: bool = True


class FolderHierarchyView(BaseModel):
    current_path: str
    current_name: str
    parent_path: Optional[str] = None
    parent_name: Optional[str] = None
    breadcrumbs: List[BreadcrumbItem]
    entries: List[FolderEntry]
    total_files: int
    total_subdirs: int
    total_size_bytes: int
    total_size_formatted: str
    is_current_deletable: bool
    target_file_path: Optional[str] = None


def get_dir_size_fast(dir_path: str, max_depth: int = 3) -> int:
    """Calculate directory size with depth limit for high UI responsiveness."""
    total = 0
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False) and max_depth > 1:
                        total += get_dir_size_fast(entry.path, max_depth=max_depth - 1)
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass
    return total


def build_breadcrumbs(folder_path: str) -> List[BreadcrumbItem]:
    """Build root-to-current breadcrumb chain for navigation and deletion."""
    norm = os.path.abspath(folder_path)
    drive, tail = os.path.splitdrive(norm)
    parts = [p for p in tail.split(os.sep) if p]

    breadcrumbs: List[BreadcrumbItem] = []
    current_accum = drive + os.sep
    # Drive root
    breadcrumbs.append(BreadcrumbItem(
        name=drive if drive else "Root",
        path=current_accum,
        is_current=(len(parts) == 0),
        is_deletable=False  # Never allow deleting drive roots
    ))

    for idx, part in enumerate(parts):
        current_accum = os.path.join(current_accum, part)
        is_last = (idx == len(parts) - 1)
        deletable = not is_system_protected_path(current_accum) and (idx > 0 or len(parts) > 1)
        breadcrumbs.append(BreadcrumbItem(
            name=part,
            path=current_accum,
            is_current=is_last,
            is_deletable=deletable
        ))

    return breadcrumbs


def inspect_path_hierarchy(input_path: str) -> FolderHierarchyView:
    """
    Given a file or folder path, returns the folder's contents and hierarchical parent tree.
    If input_path is a file, inspects its parent folder and flags the file as target_file_path.
    """
    abs_input = os.path.abspath(input_path)
    target_file_path: Optional[str] = None

    if os.path.isfile(abs_input):
        target_file_path = abs_input
        folder_path = os.path.dirname(abs_input)
    else:
        folder_path = abs_input

    folder_path = os.path.abspath(folder_path)
    folder_name = os.path.basename(folder_path) or folder_path

    # Parent calculation
    parent_path_raw = os.path.dirname(folder_path)
    parent_path = parent_path_raw if parent_path_raw and parent_path_raw != folder_path else None
    parent_name = os.path.basename(parent_path) if parent_path else None

    breadcrumbs = build_breadcrumbs(folder_path)
    is_current_deletable = not is_system_protected_path(folder_path) and (parent_path is not None)

    entries: List[FolderEntry] = []
    total_files = 0
    total_subdirs = 0
    folder_total_bytes = 0

    scan_opts = ScanOptions(target_path=folder_path, skip_system_dirs=False)

    try:
        with os.scandir(folder_path) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    stat = entry.stat(follow_symlinks=False)
                    mtime = stat.st_mtime
                    mod_dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime > 0 else "Unknown"

                    if is_dir:
                        total_subdirs += 1
                        dir_size = get_dir_size_fast(entry.path, max_depth=2)
                        folder_total_bytes += dir_size

                        # Check if directory itself is recognized as build artifact
                        garbage_item = classify_item(entry.path, entry.name, dir_size, mtime, is_dir=True, options=scan_opts)

                        entries.append(FolderEntry(
                            name=entry.name,
                            path=entry.path,
                            is_dir=True,
                            size_bytes=dir_size,
                            size_formatted=format_size(dir_size),
                            modified_date=mod_dt,
                            is_target_file=False,
                            is_garbage=(garbage_item is not None),
                            garbage_category=garbage_item.category if garbage_item else None,
                            risk_level=garbage_item.risk_level if garbage_item else None,
                            is_deletable=not is_system_protected_path(entry.path)
                        ))
                    else:
                        total_files += 1
                        size_bytes = stat.st_size
                        folder_total_bytes += size_bytes
                        is_target = (target_file_path is not None and os.path.samefile(entry.path, target_file_path))

                        garbage_item = classify_item(entry.path, entry.name, size_bytes, mtime, is_dir=False, options=scan_opts)

                        entries.append(FolderEntry(
                            name=entry.name,
                            path=entry.path,
                            is_dir=False,
                            size_bytes=size_bytes,
                            size_formatted=format_size(size_bytes),
                            modified_date=mod_dt,
                            is_target_file=is_target,
                            is_garbage=(garbage_item is not None),
                            garbage_category=garbage_item.category if garbage_item else None,
                            risk_level=garbage_item.risk_level if garbage_item else None,
                            is_deletable=not is_system_protected_path(entry.path)
                        ))
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass

    # Sort entries: directories first, then alphabetical
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))

    return FolderHierarchyView(
        current_path=folder_path,
        current_name=folder_name,
        parent_path=parent_path,
        parent_name=parent_name,
        breadcrumbs=breadcrumbs,
        entries=entries,
        total_files=total_files,
        total_subdirs=total_subdirs,
        total_size_bytes=folder_total_bytes,
        total_size_formatted=format_size(folder_total_bytes),
        is_current_deletable=is_current_deletable,
        target_file_path=target_file_path
    )
