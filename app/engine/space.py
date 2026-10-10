"""Read-only "where did my space go" summary: the largest files and folders under a path."""
from __future__ import annotations
import heapq
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.engine.classifier import format_size, is_system_protected_path
from app.engine.exclusions import ExclusionMatcher

# (size, path, mtime) for one file
_FileEntry = Tuple[int, str, float]


class _Walk:
    """Totals for one subtree: its size, file count and its largest files."""

    def __init__(self, limit: int):
        self.limit = limit
        self.bytes = 0
        self.files = 0
        self.largest: List[_FileEntry] = []  # min-heap of the `limit` biggest files

    def add_file(self, path: str, size: int, mtime: float) -> None:
        self.bytes += size
        self.files += 1
        entry = (size, path, mtime)
        if len(self.largest) < self.limit:
            heapq.heappush(self.largest, entry)
        elif size > self.largest[0][0]:
            heapq.heapreplace(self.largest, entry)


def _skip(path: str, name: str, excluder: ExclusionMatcher, skip_system_dirs: bool) -> bool:
    return (skip_system_dirs and is_system_protected_path(path)) or excluder.matches(path, name)


def _walk_tree(root: str, walk: _Walk, excluder: ExclusionMatcher, skip_system_dirs: bool, deadline: float) -> bool:
    """Add every file under root to walk. Returns False if the deadline cut it short."""
    stack = [root]
    while stack:
        if time.monotonic() > deadline:
            return False
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if _skip(entry.path, entry.name, excluder, skip_system_dirs):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            is_junction = getattr(entry, "is_junction", None)
                            if not (is_junction and is_junction()):
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            walk.add_file(entry.path, st.st_size, st.st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return True


def _file_dict(size: int, path: str, mtime: float) -> Dict[str, Any]:
    return {
        "name": os.path.basename(path),
        "path": path,
        "size_bytes": size,
        "size_formatted": format_size(size),
        "modified_timestamp": mtime,
        "modified_date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime > 0 else "Unknown",
    }


def largest_items(
    root: str,
    file_limit: int = 50,
    folder_limit: int = 25,
    exclusions: Iterable[str] = (),
    skip_system_dirs: bool = True,
    max_workers: int = 8,
    time_limit: Optional[float] = 300.0,
) -> Dict[str, Any]:
    """Return the biggest files under root and the sizes of root's immediate subfolders.

    Nothing is deleted. Protected system folders and excluded paths are not counted,
    so on a system drive the totals cover what JunkZero is allowed to clean.
    """
    started = time.monotonic()
    deadline = started + time_limit if time_limit else float("inf")
    root = os.path.abspath(root)
    excluder = ExclusionMatcher(exclusions)

    top_files = _Walk(file_limit)  # files directly inside root
    subdirs: List[Tuple[str, float]] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    if _skip(entry.path, entry.name, excluder, skip_system_dirs):
                        continue
                    st = entry.stat(follow_symlinks=False)
                    if entry.is_dir(follow_symlinks=False):
                        is_junction = getattr(entry, "is_junction", None)
                        if not (is_junction and is_junction()):
                            subdirs.append((entry.path, st.st_mtime))
                    elif entry.is_file(follow_symlinks=False):
                        top_files.add_file(entry.path, st.st_size, st.st_mtime)
                except OSError:
                    continue
    except OSError as e:
        raise ValueError(f"Cannot read {root}: {e.strerror or e}") from e

    def walk_subdir(path: str) -> Tuple[_Walk, bool]:
        walk = _Walk(file_limit)
        complete = _walk_tree(path, walk, excluder, skip_system_dirs, deadline)
        return walk, complete

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(walk_subdir, [p for p, _ in subdirs]))

    folders = []
    all_files: List[_FileEntry] = list(top_files.largest)
    complete = True
    for (path, mtime), (walk, done) in zip(subdirs, results):
        complete = complete and done
        all_files.extend(walk.largest)
        folders.append({
            "name": os.path.basename(path),
            "path": path,
            "size_bytes": walk.bytes,
            "size_formatted": format_size(walk.bytes),
            "file_count": walk.files,
            "modified_timestamp": mtime,
        })

    folders.sort(key=lambda f: f["size_bytes"], reverse=True)
    biggest = heapq.nlargest(file_limit, all_files)
    total = top_files.bytes + sum(w.bytes for w, _ in results)

    return {
        "root": root,
        "total_bytes": total,
        "total_formatted": format_size(total),
        "file_count": top_files.files + sum(w.files for w, _ in results),
        "loose_files_bytes": top_files.bytes,
        "folders": folders[:folder_limit],
        "files": [_file_dict(*f) for f in biggest],
        "complete": complete,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
