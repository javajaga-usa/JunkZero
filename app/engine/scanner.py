"""High-performance multi-threaded directory scanner."""
from __future__ import annotations
import os
import shutil
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from app.config import (
    BUILD_DIR_NAMES,
    CAT_EMPTY_FOLDERS,
    RISK_SAFE,
    ScanOptions,
    SYSTEM_BLACKLIST_DIRS,
)
from app.engine.classifier import (
    GarbageItem,
    classify_item,
    format_size,
    is_system_protected_path,
)


@dataclass
class ScanStats:
    total_files_scanned: int = 0
    total_dirs_scanned: int = 0
    garbage_items_found: int = 0
    total_garbage_bytes: int = 0
    category_counts: Dict[str, int] = field(default_factory=dict)
    category_bytes: Dict[str, int] = field(default_factory=dict)
    start_time: float = 0.0
    elapsed_seconds: float = 0.0
    is_running: bool = False
    is_completed: bool = False
    is_cancelled: bool = False
    drive_total_bytes: int = 0
    drive_used_bytes: int = 0
    drive_free_bytes: int = 0
    target_path: str = ""


class FastScanner:
    """Multi-threaded filesystem scanner optimized for Windows I/O."""

    def __init__(self, options: ScanOptions):
        self.options = options
        self.stats = ScanStats(target_path=options.target_path)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.garbage_items: List[GarbageItem] = []
        self._callback: Optional[Callable[[GarbageItem, ScanStats], None]] = None
        # Per-directory record used to detect empty folders after traversal:
        # path -> (has_non_folder_content, child_dir_paths). Insertion order is
        # parent-before-child because a child is only discovered by scanning its parent.
        self._dir_tree: Dict[str, tuple[bool, List[str]]] = {}

    def cancel(self) -> None:
        """Cancel ongoing scan gracefully."""
        self._stop_event.set()
        with self._lock:
            self.stats.is_cancelled = True
            self.stats.is_running = False

    def set_callback(self, callback: Callable[[GarbageItem, ScanStats], None]) -> None:
        """Set a real-time event callback triggered when a garbage item is found."""
        self._callback = callback

    def _calc_dir_size(self, dir_path: str) -> int:
        """Quickly compute total size of a directory entry."""
        total = 0
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    if self._stop_event.is_set():
                        break
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            total += self._calc_dir_size(entry.path)
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
        except (PermissionError, FileNotFoundError, OSError):
            pass
        return total

    def _scan_directory_shallow(self, dir_path: str) -> tuple[List[GarbageItem], List[str]]:
        """
        Scan a single directory level using os.scandir.
        Returns:
            (list of found garbage items, list of valid subdirectories to scan next)
        """
        found_items: List[GarbageItem] = []
        subdirs: List[str] = []
        # Anything other than a plain, traversable subfolder (files, links, build dirs,
        # protected or unreadable entries) means this folder is not empty.
        has_content = False

        if self._stop_event.is_set():
            return found_items, subdirs

        try:
            with os.scandir(dir_path) as entries:
                for entry in entries:
                    if self._stop_event.is_set():
                        break

                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        is_file = entry.is_file(follow_symlinks=False)
                        name = entry.name
                        path = entry.path

                        # Check system blacklist
                        if self.options.skip_system_dirs and is_system_protected_path(path):
                            has_content = True
                            continue

                        # Read metadata
                        stat = entry.stat(follow_symlinks=False)
                        mtime = stat.st_mtime

                        if is_dir:
                            with self._lock:
                                self.stats.total_dirs_scanned += 1

                            # Windows junctions are not followed for emptiness purposes
                            is_junction = getattr(entry, "is_junction", None)
                            if is_junction and is_junction():
                                has_content = True

                            # Check if the directory itself is a build artifact (e.g. node_modules, target, __pycache__)
                            if name.lower() in BUILD_DIR_NAMES:
                                has_content = True
                            if self.options.include_java_builds and name.lower() in BUILD_DIR_NAMES:
                                dir_size = self._calc_dir_size(path)
                                item = classify_item(path, name, dir_size, mtime, is_dir=True, options=self.options)
                                if item:
                                    found_items.append(item)
                                # Do not recurse inside marked build directory
                                continue

                            subdirs.append(path)

                        elif is_file:
                            has_content = True
                            with self._lock:
                                self.stats.total_files_scanned += 1

                            size = stat.st_size
                            item = classify_item(path, name, size, mtime, is_dir=False, options=self.options)
                            if item:
                                found_items.append(item)

                        else:
                            # Symlinks and other special entries
                            has_content = True

                    except (PermissionError, FileNotFoundError, OSError):
                        has_content = True
                        continue

        except (PermissionError, FileNotFoundError, OSError):
            has_content = True

        if self._stop_event.is_set():
            # Partial listing: never treat this folder as empty
            has_content = True

        with self._lock:
            self._dir_tree[dir_path] = (has_content, list(subdirs))

        return found_items, subdirs

    def _record_item(self, item: GarbageItem) -> None:
        """Add a found item to results and stats, and notify the callback. Caller holds the lock."""
        self.garbage_items.append(item)
        self.stats.garbage_items_found += 1
        self.stats.total_garbage_bytes += item.size_bytes

        cat = item.category
        self.stats.category_counts[cat] = self.stats.category_counts.get(cat, 0) + 1
        self.stats.category_bytes[cat] = self.stats.category_bytes.get(cat, 0) + item.size_bytes

        if self._callback:
            self._callback(item, self.stats)

    def _find_empty_folders(self, root_path: str) -> List[GarbageItem]:
        """
        Return folders that contain no files at any depth, reporting only the topmost
        folder of each empty tree. The scan root itself is never reported.
        """
        empty: Dict[str, bool] = {}
        nested_count: Dict[str, int] = {}
        parent_of: Dict[str, str] = {}

        # Children were recorded after their parents, so reverse order is bottom-up.
        for dir_path, (has_content, children) in reversed(list(self._dir_tree.items())):
            for child in children:
                parent_of[child] = dir_path
            is_empty = not has_content and all(empty.get(c, False) for c in children)
            empty[dir_path] = is_empty
            if is_empty:
                nested_count[dir_path] = sum(1 + nested_count.get(c, 0) for c in children)

        items: List[GarbageItem] = []
        for dir_path, is_empty in empty.items():
            if not is_empty or dir_path == root_path:
                continue
            parent = parent_of.get(dir_path)
            if parent is None:
                continue
            if parent != root_path and empty.get(parent, False):
                continue  # Reported as part of its empty parent

            try:
                mtime = os.stat(dir_path, follow_symlinks=False).st_mtime
            except OSError:
                continue

            nested = nested_count.get(dir_path, 0)
            if nested:
                reason = f"Empty folder tree ({nested} nested empty subfolder{'s' if nested != 1 else ''}, no files)"
            else:
                reason = "Empty folder (contains no files)"

            items.append(GarbageItem(
                id=f"empty-{abs(hash(dir_path))}",
                name=os.path.basename(dir_path),
                path=dir_path,
                category=CAT_EMPTY_FOLDERS,
                size_bytes=0,
                size_formatted=format_size(0),
                modified_timestamp=mtime,
                modified_date=datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime > 0 else "Unknown",
                risk_level=RISK_SAFE,
                reason=reason,
                is_directory=True,
                selected=True,
            ))

        return items

    def run_scan(self) -> List[GarbageItem]:
        """Execute parallel multi-threaded scan starting at options.target_path."""
        self.stats = ScanStats(target_path=self.options.target_path)
        self.stats.start_time = time.time()
        self.stats.is_running = True
        self.stats.is_completed = False
        self.stats.is_cancelled = self._stop_event.is_set()
        self.garbage_items.clear()
        self._dir_tree.clear()

        # Gather drive usage
        try:
            drive_stat = shutil.disk_usage(self.options.target_path)
            self.stats.drive_total_bytes = drive_stat.total
            self.stats.drive_used_bytes = drive_stat.used
            self.stats.drive_free_bytes = drive_stat.free
        except Exception:
            pass

        root_path = os.path.abspath(self.options.target_path)
        if not os.path.exists(root_path):
            self.stats.is_running = False
            self.stats.is_completed = True
            return []

        # Directory queue to scan
        pending_dirs: List[str] = [root_path]

        # Use ThreadPoolExecutor for concurrent batch scanning
        with ThreadPoolExecutor(max_workers=self.options.max_workers) as executor:
            while pending_dirs and not self._stop_event.is_set():
                # Take batch of directories to scan concurrently
                batch = pending_dirs[:32]
                pending_dirs = pending_dirs[32:]

                futures = {executor.submit(self._scan_directory_shallow, d): d for d in batch}

                for future in as_completed(futures):
                    if self._stop_event.is_set():
                        break
                    try:
                        items, new_subdirs = future.result()
                        pending_dirs.extend(new_subdirs)

                        if items:
                            with self._lock:
                                for item in items:
                                    self._record_item(item)
                    except Exception:
                        continue

        # Empty folders can only be known once the whole tree has been traversed
        if self.options.include_empty_folders and not self._stop_event.is_set():
            empty_items = self._find_empty_folders(root_path)
            with self._lock:
                for item in empty_items:
                    self._record_item(item)

        with self._lock:
            self.stats.elapsed_seconds = round(time.time() - self.stats.start_time, 2)
            self.stats.is_running = False
            self.stats.is_completed = not self._stop_event.is_set()

        return self.garbage_items


def get_available_drives() -> List[Dict[str, any]]:
    """Retrieve list of accessible drives on Windows with disk usage statistics."""
    drives = []
    # Windows drive letters A-Z
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        drive_path = f"{letter}:\\"
        if os.path.exists(drive_path):
            try:
                usage = shutil.disk_usage(drive_path)
                drives.append({
                    "drive": drive_path,
                    "label": f"Local Disk ({letter}:)",
                    "total_bytes": usage.total,
                    "total_formatted": format_size(usage.total),
                    "used_bytes": usage.used,
                    "used_formatted": format_size(usage.used),
                    "free_bytes": usage.free,
                    "free_formatted": format_size(usage.free),
                    "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0
                })
            except (PermissionError, OSError):
                drives.append({
                    "drive": drive_path,
                    "label": f"Drive ({letter}:)",
                    "total_bytes": 0,
                    "total_formatted": "N/A",
                    "used_bytes": 0,
                    "used_formatted": "N/A",
                    "free_bytes": 0,
                    "free_formatted": "N/A",
                    "used_percent": 0
                })
    return drives
