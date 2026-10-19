"""Duplicate folders: whole folders holding exactly the same files (a photo folder copied twice).

The scan records each folder's files (name and size) and subfolders. Folders with the same
names, sizes and layout at every depth are then compared by content, file by file. A folder is
listed only when:
  * the scan saw everything in it (nothing skipped, excluded, unreadable, linked or online-only),
  * it holds at least two files and DUPLICATE_MIN_BYTES in all,
  * every file in it matches the file at the same place in the other copy, byte for byte.
Only the outermost duplicate is listed (not each of its subfolders again), and the copy in a
personal or cloud folder is kept, as for duplicate files. Before deleting, both copies are
compared again.
"""
from __future__ import annotations
import hashlib
import os
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from app.config import DUPLICATE_MIN_BYTES
from app.engine.duplicates import _hash

MIN_FILES = 2


@dataclass
class FolderInfo:
    path: str
    signature: str     # Same for folders with the same names, sizes and layout
    size: int
    files: int


def signatures(children: Dict[str, List[str]], files: Dict[str, List[Tuple[str, int]]],
               incomplete: Set[str]) -> Dict[str, FolderInfo]:
    """Layout signature of every folder the scan saw completely. children must list parents
    before their children (the scan's order)."""
    infos: Dict[str, FolderInfo] = {}
    unsure: Set[str] = set()
    for folder in reversed(list(children)):
        subs = children.get(folder) or []
        if folder in incomplete or folder not in files or any(s in unsure or s not in infos for s in subs):
            unsure.add(folder)
            continue
        entries = sorted(("f", name, size) for name, size in files[folder])
        entries += sorted(("d", os.path.basename(s), infos[s].signature) for s in subs)
        digest = hashlib.blake2b(repr(entries).encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()
        size = sum(size for _, size in files[folder]) + sum(infos[s].size for s in subs)
        count = len(files[folder]) + sum(infos[s].files for s in subs)
        infos[folder] = FolderInfo(folder, digest, size, count)
    return infos


def _within(path: str, folder: str) -> bool:
    a, b = os.path.normcase(path), os.path.normcase(folder)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def content_digest(folder: str, stop: Optional[threading.Event] = None) -> Optional[str]:
    """Hash of every file's place and contents in the folder (None if any can't be read)."""
    h = hashlib.blake2b(digest_size=20)
    for root, dirs, names in os.walk(folder):
        dirs.sort()
        if any(os.path.islink(os.path.join(root, d)) for d in dirs):
            return None
        for name in sorted(names):
            if stop is not None and stop.is_set():
                return None
            path = os.path.join(root, name)
            if os.path.islink(path):
                return None
            digest = _hash(path, None, stop)
            if digest is None:
                return None
            h.update(os.path.relpath(path, folder).replace("\\", "/").encode("utf-8", "surrogatepass") + b"\0")
            h.update(digest.encode() + b"\0")
    return h.hexdigest()


def find_groups(infos: Dict[str, FolderInfo], allowed: Callable[[str], bool],
                stop: Optional[threading.Event] = None) -> List[List[FolderInfo]]:
    """Groups (2+) of folders with identical contents, outermost first; a folder inside one
    already in a group is not offered again."""
    by_sig: Dict[str, List[FolderInfo]] = defaultdict(list)
    for info in infos.values():
        if info.files >= MIN_FILES and info.size >= DUPLICATE_MIN_BYTES and allowed(info.path):
            by_sig[info.signature].append(info)
    # Biggest first, and of equal size the outermost first (a folder holding only one subfolder
    # is as big as that subfolder), so a copy's subfolders are never listed instead of it
    candidates = sorted((g for g in by_sig.values() if len(g) >= 2),
                        key=lambda g: (-g[0].size, min(i.path.count(os.sep) for i in g)))
    found: List[List[FolderInfo]] = []
    taken: List[str] = []
    for group in candidates:
        if stop is not None and stop.is_set():
            return []
        group = [i for i in group if not any(_within(i.path, t) for t in taken)]
        # A folder and one inside it can't be copies of each other
        group = [i for i in group if not any(o is not i and _within(i.path, o.path) for o in group)]
        if len(group) < 2:
            continue
        by_content: Dict[str, List[FolderInfo]] = defaultdict(list)
        for info in group:
            digest = content_digest(info.path, stop)
            if digest is not None:
                by_content[digest].append(info)
        for same in by_content.values():
            if len(same) >= 2:
                found.append(same)
                taken.extend(i.path for i in same)
    return found


def _listing(folder: str) -> Optional[Tuple[Dict[str, int], Set[str]]]:
    """({relative file path: size}, {relative folder paths}), or None if it can't all be read."""
    files: Dict[str, int] = {}
    dirs_seen: Set[str] = set()
    try:
        for root, dirs, names in os.walk(folder, onerror=_raise):
            for d in dirs:
                path = os.path.join(root, d)
                if os.path.islink(path):
                    return None
                dirs_seen.add(os.path.relpath(path, folder))
            for name in names:
                path = os.path.join(root, name)
                if os.path.islink(path):
                    return None
                files[os.path.relpath(path, folder)] = os.lstat(path).st_size
    except OSError:
        return None
    return files, dirs_seen


def _raise(error: OSError) -> None:
    raise error


def same_folder(a: str, b: str, same_file: Callable[[str, str], bool]) -> bool:
    """True if both folders exist and hold exactly the same files, byte for byte (checked
    again right before deleting one of them)."""
    if not (os.path.isdir(a) and os.path.isdir(b)) or os.path.islink(a) or os.path.islink(b):
        return False
    try:
        if os.path.samefile(a, b):
            return False
    except OSError:
        return False
    if _within(a, b) or _within(b, a):
        return False
    first, second = _listing(a), _listing(b)
    if first is None or second is None or first != second or not first[0]:
        return False
    return all(same_file(os.path.join(a, rel), os.path.join(b, rel)) for rel in first[0])


def inside_any(path: str, folders: Iterable[str]) -> bool:
    return any(_within(path, f) for f in folders)
