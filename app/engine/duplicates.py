"""Duplicate file detection: group by size, then by a partial hash, then by a full hash."""
from __future__ import annotations
import hashlib
import os
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

PARTIAL_BYTES = 64 * 1024
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class FileCandidate:
    path: str
    name: str
    size: int
    mtime: float


def _hash(path: str, limit: Optional[int], stop: Optional[threading.Event]) -> Optional[str]:
    h = hashlib.blake2b(digest_size=20)
    remaining = limit
    try:
        with open(path, "rb") as f:
            while True:
                if stop is not None and stop.is_set():
                    return None
                size = CHUNK_BYTES if remaining is None else min(CHUNK_BYTES, remaining)
                if size <= 0:
                    break
                chunk = f.read(size)
                if not chunk:
                    break
                h.update(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _distinct_files(group: List[FileCandidate]) -> List[FileCandidate]:
    """Drop entries that are the same file on disk (same path or hard link)."""
    seen_paths = set()
    seen_ids = set()
    result = []
    for c in group:
        key = os.path.normcase(os.path.realpath(c.path))
        if key in seen_paths:
            continue
        try:
            st = os.stat(c.path)
        except OSError:
            continue
        if st.st_ino:
            file_id = (st.st_dev, st.st_ino)
            if file_id in seen_ids:
                continue
            seen_ids.add(file_id)
        seen_paths.add(key)
        result.append(c)
    return result


def find_duplicate_groups(
    candidates: List[FileCandidate],
    stop: Optional[threading.Event] = None,
) -> List[List[FileCandidate]]:
    """Return groups (2+ files each) whose contents are byte-for-byte identical by hash."""
    by_size: Dict[int, List[FileCandidate]] = defaultdict(list)
    for c in candidates:
        if c.size > 0:
            by_size[c.size].append(c)

    groups: List[List[FileCandidate]] = []
    for size, same_size in by_size.items():
        if len(same_size) < 2:
            continue
        same_size = _distinct_files(same_size)
        if len(same_size) < 2:
            continue

        by_partial: Dict[str, List[FileCandidate]] = defaultdict(list)
        for c in same_size:
            digest = _hash(c.path, PARTIAL_BYTES, stop)
            if digest is not None:
                by_partial[digest].append(c)

        for partial_group in by_partial.values():
            if len(partial_group) < 2:
                continue
            if size <= PARTIAL_BYTES:
                groups.append(partial_group)
                continue
            by_full: Dict[str, List[FileCandidate]] = defaultdict(list)
            for c in partial_group:
                digest = _hash(c.path, None, stop)
                if digest is not None:
                    by_full[digest].append(c)
            groups.extend(g for g in by_full.values() if len(g) >= 2)

        if stop is not None and stop.is_set():
            return []

    return groups


def pick_keeper(group: List[FileCandidate]) -> FileCandidate:
    """Keep the most recently modified copy (ties broken by shortest, then alphabetical path)."""
    return min(group, key=lambda c: (-c.mtime, len(c.path), c.path))
