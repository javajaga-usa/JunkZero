"""High-performance multi-threaded directory scanner."""
from __future__ import annotations
import fnmatch
import os
import shutil
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from app.config import (
    BUILD_DIR_NAMES,
    CAT_CUSTOM_RULES,
    CAT_DUPLICATES,
    CAT_EMPTY_FOLDERS,
    CAT_LEFTOVERS,
    CAT_OLD_DOWNLOADS,
    CAT_TEMP_JUNK,
    DUPLICATE_MIN_BYTES,
    RISK_REVIEW,
    RISK_SAFE,
    ScanOptions,
    SYSTEM_BLACKLIST_DIRS,
    VCS_DIR_NAMES,
)
from app.engine.classifier import (
    GarbageItem,
    build_item,
    classify_item,
    format_size,
    is_system_protected_path,
)
from app.engine.duplicates import FileCandidate, find_duplicate_groups, pick_keeper
from app.engine.exclusions import ExclusionMatcher
from app.engine import osinfo
from app.engine.leftovers import find_program_leftovers
from app.engine.smart import SmartScorer


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


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
    """Multi-threaded filesystem scanner."""

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
        self._hidden_dirs: Set[str] = set()
        self._excluder = ExclusionMatcher(options.exclusions)
        self._junk_rules = ExclusionMatcher(options.custom_rules if options.include_custom_rules else [])
        # Junk locations: folders flagged as a single item, and folders whose files are flagged
        self._dir_locations = {
            _norm_path(loc.path): loc for loc in options.junk_locations if loc.whole_dir
        }
        self._file_locations = [
            (_norm_path(loc.path), loc) for loc in options.junk_locations if not loc.whole_dir
        ]
        self._downloads_dirs = [_norm_path(p) for p in options.downloads_dirs if p]
        # Unflagged files large enough to be checked for duplicates after traversal
        self._dup_candidates: List[FileCandidate] = []
        # Program leftover folders (path -> Leftover), found when the scan starts
        self._leftovers: Dict[str, object] = {}
        self._scorer = SmartScorer(options.learning)

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

    def _file_locations_for(self, dir_path: str) -> List:
        """Junk locations (flagging individual files) that contain dir_path."""
        if not self._file_locations or not self.options.include_temp_junk:
            return []
        norm = _norm_path(dir_path)
        return [loc for root, loc in self._file_locations if norm == root or norm.startswith(root + os.sep)]

    def _in_downloads(self, dir_path: str) -> bool:
        if not self._downloads_dirs or not self.options.include_old_downloads:
            return False
        norm = _norm_path(dir_path)
        return any(norm == root or norm.startswith(root.rstrip(os.sep) + os.sep) for root in self._downloads_dirs)

    def _old_download_item(self, path: str, name: str, size: int, stat: os.stat_result) -> Optional[GarbageItem]:
        """Flag a file in Downloads that hasn't been modified (or copied in) for a while."""
        if size < self.options.min_file_size_bytes:
            return None
        # A file copied in recently keeps its old mtime, so also use the creation time
        # (st_birthtime on macOS and Windows, or st_ctime on older Windows Pythons) and take whichever is newer.
        created = getattr(stat, "st_birthtime", None) or stat.st_ctime
        if osinfo.is_macos():
            # macOS moves the creation date back when a copy keeps an old modified date, but
            # the change time (st_ctime) still shows when the file was copied, moved or downloaded
            created = max(created, stat.st_ctime)
        last_touched = max(stat.st_mtime, created)
        age_days = (time.time() - last_touched) / 86400.0
        if last_touched <= 0 or age_days < self.options.old_download_days:
            return None
        return build_item(
            path, name, CAT_OLD_DOWNLOADS, size, stat.st_mtime, RISK_REVIEW,
            f"In Downloads, untouched for {int(age_days)} days", selected=False,
        )

    def _custom_rule_item(self, path: str, name: str, size: int, mtime: float, is_dir: bool = False) -> Optional[GarbageItem]:
        """Flag something that matches one of the user's own junk rules (never preselected)."""
        rule = self._junk_rules.match(path, name)
        if rule is None or size < self.options.min_file_size_bytes:
            return None
        return build_item(
            path, name, CAT_CUSTOM_RULES, size, mtime, RISK_REVIEW,
            f"Matches your junk rule: {rule}", is_dir=is_dir, selected=False,
        )

    def _location_item(self, path: str, name: str, size: int, mtime: float, locations: List) -> Optional[GarbageItem]:
        """Flag a file that sits inside a known junk location."""
        if size < self.options.min_file_size_bytes:
            return None
        for loc in locations:
            if loc.pattern and not fnmatch.fnmatchcase(name.lower(), loc.pattern):
                continue
            recent = mtime > 0 and (time.time() - mtime) < 86400
            reason = f"File in {loc.label}"
            if recent:
                reason += " (changed in the last 24 hours, may still be in use)"
            return build_item(path, name, CAT_TEMP_JUNK, size, mtime, RISK_REVIEW if recent else RISK_SAFE, reason)
        return None

    def _scan_directory_shallow(self, dir_path: str) -> tuple[List[GarbageItem], List[str]]:
        """
        Scan a single directory level using os.scandir.
        Returns:
            (list of found garbage items, list of valid subdirectories to scan next)
        """
        found_items: List[GarbageItem] = []
        subdirs: List[str] = []
        dup_candidates: List[FileCandidate] = []
        file_locations = self._file_locations_for(dir_path)
        in_downloads = self._in_downloads(dir_path)
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

                        # Version-control folders hold state the tool needs, even empty folders
                        if name.lower() in VCS_DIR_NAMES:
                            has_content = True
                            continue

                        # User exclusions: never flagged, never descended into
                        if self._excluder.matches(path, name):
                            has_content = True
                            continue

                        # Read metadata
                        stat = entry.stat(follow_symlinks=False)
                        mtime = stat.st_mtime

                        if is_dir:
                            with self._lock:
                                self.stats.total_dirs_scanned += 1

                            # A hidden subfolder (even an empty one) means this folder is not
                            # empty, and is never reported as empty itself: apps create them on purpose
                            if osinfo.is_hidden(name, stat):
                                has_content = True
                                with self._lock:
                                    self._hidden_dirs.add(path)

                            # Windows junctions are not followed for emptiness purposes
                            is_junction = getattr(entry, "is_junction", None)
                            if is_junction and is_junction():
                                has_content = True

                            leftover = self._leftovers.get(_norm_path(path)) if self._leftovers else None
                            if leftover:
                                has_content = True
                                found_items.append(self._leftover_item(leftover))
                                continue

                            # Check if the directory itself is a build artifact (e.g. node_modules, target, __pycache__)
                            # Known cache folders that apps rebuild are flagged as one item
                            location = self._dir_locations.get(_norm_path(path)) if self.options.include_temp_junk else None
                            if location:
                                has_content = True
                                found_items.append(build_item(
                                    path, name, CAT_TEMP_JUNK, self._calc_dir_size(path), mtime, RISK_SAFE,
                                    f"{location.label} (rebuilt automatically; close the app first)", is_dir=True,
                                ))
                                continue

                            if name.lower() in BUILD_DIR_NAMES:
                                has_content = True
                            if self.options.include_java_builds and name.lower() in BUILD_DIR_NAMES:
                                dir_size = self._calc_dir_size(path)
                                item = classify_item(path, name, dir_size, mtime, is_dir=True, options=self.options)
                                if item:
                                    found_items.append(item)
                                # Do not recurse inside marked build directory
                                continue

                            # A folder matching a user junk rule is listed as one item
                            if self._junk_rules and self._junk_rules.matches(path, name):
                                has_content = True
                                item = self._custom_rule_item(path, name, self._calc_dir_size(path), mtime, is_dir=True)
                                if item:
                                    found_items.append(item)
                                continue

                            subdirs.append(path)

                        elif is_file:
                            has_content = True
                            with self._lock:
                                self.stats.total_files_scanned += 1

                            size = stat.st_size
                            item = self._location_item(path, name, size, mtime, file_locations) if file_locations else None
                            if item is None:
                                item = classify_item(path, name, size, mtime, is_dir=False, options=self.options)
                            if item is None and in_downloads:
                                item = self._old_download_item(path, name, size, stat)
                            if item is None and self._junk_rules:
                                item = self._custom_rule_item(path, name, size, mtime)
                            if item:
                                found_items.append(item)
                            elif self.options.include_duplicates and size >= DUPLICATE_MIN_BYTES:
                                dup_candidates.append(FileCandidate(path, name, size, mtime))

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
            self._dup_candidates.extend(dup_candidates)

        return found_items, subdirs

    def _leftover_item(self, leftover) -> GarbageItem:
        return build_item(
            leftover.path, leftover.name, CAT_LEFTOVERS, self._calc_dir_size(leftover.path), leftover.last_changed,
            RISK_REVIEW, f"App data from a program that is no longer installed; nothing changed for {leftover.days_unchanged} days",
            is_dir=True, selected=False,
        )

    def _load_leftovers(self, roots: List[str]) -> List[GarbageItem]:
        """Find leftover folders; returns those outside every root (traversal finds the rest)."""
        found = self.options.leftovers if self.options.leftovers is not None else find_program_leftovers()
        self._leftovers = {}
        for leftover in found:
            if self._excluder.matches(leftover.path, leftover.name):
                continue
            if self.options.skip_system_dirs and is_system_protected_path(leftover.path):
                continue
            self._leftovers[_norm_path(leftover.path)] = leftover
        return [self._leftover_item(l) for l in self._leftovers.values()
                if not any(self._is_within(l.path, r) for r in roots)]

    def _record_item(self, item: GarbageItem) -> None:
        """Add a found item to results and stats, and notify the callback. Caller holds the lock."""
        self._scorer.apply(item)
        self.garbage_items.append(item)
        self.stats.garbage_items_found += 1
        self.stats.total_garbage_bytes += item.size_bytes

        cat = item.category
        self.stats.category_counts[cat] = self.stats.category_counts.get(cat, 0) + 1
        self.stats.category_bytes[cat] = self.stats.category_bytes.get(cat, 0) + item.size_bytes

        if self._callback:
            self._callback(item, self.stats)

    def _find_empty_folders(self, root_paths: str | Iterable[str]) -> List[GarbageItem]:
        """
        Return folders that contain nothing at any depth (hidden files and hidden subfolders
        count as content), reporting only the topmost folder of each empty tree. The scan
        roots and hidden folders themselves are never reported.
        """
        roots = {root_paths} if isinstance(root_paths, str) else set(root_paths)
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
            if not is_empty or dir_path in roots or dir_path in self._hidden_dirs:
                continue
            parent = parent_of.get(dir_path)
            if parent is None:
                continue
            if parent not in roots and empty.get(parent, False):
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
                # Some apps expect their (empty) folders to exist, so these are never preselected
                selected=False,
            ))

        return items

    @staticmethod
    def _is_within(path: str, root: str) -> bool:
        norm, norm_root = _norm_path(path), _norm_path(root)
        return norm == norm_root or norm.startswith(norm_root.rstrip(os.sep) + os.sep)

    def _outside_location_items(self, roots: List[str]) -> List[GarbageItem]:
        items: List[GarbageItem] = []
        for loc in self.options.junk_locations:
            if not loc.whole_dir or any(self._is_within(loc.path, r) for r in roots):
                continue
            if not os.path.isdir(loc.path) or self._excluder.matches(loc.path):
                continue
            if self.options.skip_system_dirs and is_system_protected_path(loc.path):
                continue
            try:
                mtime = os.stat(loc.path, follow_symlinks=False).st_mtime
            except OSError:
                continue
            items.append(build_item(
                loc.path, os.path.basename(loc.path), CAT_TEMP_JUNK, self._calc_dir_size(loc.path), mtime,
                RISK_SAFE, f"{loc.label} (rebuilt automatically; close the app first)", is_dir=True,
            ))
        return items

    def _scan_roots(self, paths: List[str]) -> List[str]:
        """Existing scan roots, skipping any nested inside another root."""
        roots: List[str] = []
        for path in paths:
            path = os.path.abspath(path)
            if not os.path.isdir(path):
                continue
            if any(self._is_within(path, r) for r in roots):
                continue
            # A new root may contain earlier ones; replace them
            roots = [r for r in roots if not self._is_within(r, path)]
            roots.append(path)
        return roots

    def _find_duplicates(self) -> List[GarbageItem]:
        """Flag every copy but the newest in each group of identical files."""
        items: List[GarbageItem] = []
        for group in find_duplicate_groups(self._dup_candidates, self._stop_event):
            keep = pick_keeper(group)
            for c in group:
                if c is keep:
                    continue
                items.append(build_item(
                    c.path, c.name, CAT_DUPLICATES, c.size, c.mtime, RISK_REVIEW,
                    f"Identical copy of {keep.path} (newest copy is kept)", selected=False,
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
        self._dup_candidates.clear()

        # Gather drive usage
        try:
            drive_stat = shutil.disk_usage(self.options.target_path)
            self.stats.drive_total_bytes = drive_stat.total
            self.stats.drive_used_bytes = drive_stat.used
            self.stats.drive_free_bytes = drive_stat.free
        except Exception:
            pass

        root_path = os.path.abspath(self.options.target_path) if self.options.target_path else None
        if root_path and not os.path.exists(root_path):
            self.stats.is_running = False
            self.stats.is_completed = True
            return []

        extra_paths = list(self.options.extra_paths)
        if self.options.scan_junk_locations and self.options.include_temp_junk:
            extra_paths += [loc.path for loc in self.options.junk_locations if not loc.whole_dir]
        roots = self._scan_roots([root_path, *extra_paths] if root_path else extra_paths)

        # Cache folders outside every root are flagged directly; inside a root, traversal finds them
        if self.options.scan_junk_locations and self.options.include_temp_junk:
            for item in self._outside_location_items(roots):
                with self._lock:
                    self._record_item(item)

        if self.options.include_leftovers and not self._stop_event.is_set():
            for item in self._load_leftovers(roots):
                with self._lock:
                    self._record_item(item)

        # Directory queue to scan
        pending_dirs: List[str] = list(roots)

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
            empty_items = self._find_empty_folders(roots)
            with self._lock:
                for item in empty_items:
                    self._record_item(item)

        # Duplicates need every candidate file, so they are also found after traversal
        if self.options.include_duplicates and not self._stop_event.is_set():
            for item in self._find_duplicates():
                with self._lock:
                    self._record_item(item)

        with self._lock:
            self.stats.elapsed_seconds = round(time.time() - self.stats.start_time, 2)
            self.stats.is_running = False
            self.stats.is_completed = not self._stop_event.is_set()

        return self.garbage_items


def _drive_info(path: str, label: str) -> Dict[str, any]:
    usage = shutil.disk_usage(path)
    return {
        "drive": path,
        "label": label,
        "total_bytes": usage.total,
        "total_formatted": format_size(usage.total),
        "used_bytes": usage.used,
        "used_formatted": format_size(usage.used),
        "free_bytes": usage.free,
        "free_formatted": format_size(usage.free),
        "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0
    }


def get_mac_drives(volumes_dir: str = "/Volumes") -> List[Dict[str, any]]:
    """The startup disk ("/") and other mounted disks in /Volumes (external drives, USB sticks)."""
    drives = []
    try:
        drives.append(_drive_info("/", "Macintosh HD"))
        root_dev = os.stat("/").st_dev
    except OSError:
        root_dev = None
    try:
        entries = sorted(os.scandir(volumes_dir), key=lambda e: e.name.lower())
    except OSError:
        entries = []
    for entry in entries:
        try:
            # The startup disk also appears in /Volumes (as a link to "/"); skip it
            if entry.name.startswith(".") or not entry.is_dir() or os.stat(entry.path).st_dev == root_dev:
                continue
            drives.append(_drive_info(entry.path, entry.name))
        except OSError:
            continue
    return drives


def get_available_drives() -> List[Dict[str, any]]:
    """Retrieve list of accessible drives (Windows drive letters, or macOS disks) with disk usage statistics."""
    if osinfo.is_macos():
        return get_mac_drives()
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
