"""Free space over time: a point per disk each time JunkZero scans or cleans (at most one an
hour), for the chart in Cleanup History. Nothing else is recorded."""
from __future__ import annotations
import os
import shutil
import time
from typing import Any, Dict, Iterable, List, Optional

from app.engine import storage

LOG_FILE = "space_log.json"
POINTS_PER_DISK = 400     # About a year of daily scans
MIN_GAP = 3600            # A newer point within an hour replaces the last one
DISKS_LIMIT = 20


def _root(path: str) -> str:
    from app.engine.recycle import mount_point
    return mount_point(path)


def _load() -> Dict[str, List[Dict[str, float]]]:
    data = storage.read_json(LOG_FILE, {})
    return data if isinstance(data, dict) else {}


def record(path: str, total: Optional[int] = None, free: Optional[int] = None, now: Optional[float] = None) -> None:
    """Note the free space of the disk holding path. Never raises."""
    try:
        root = _root(path)
        if total is None or free is None:
            usage = shutil.disk_usage(root)
            total, free = usage.total, usage.free
        now = time.time() if now is None else now
        with storage.lock():
            data = _load()
            points = [p for p in data.get(root) or [] if isinstance(p, dict)]
            point = {"at": now, "total": int(total), "free": int(free)}
            if points and 0 <= now - float(points[-1].get("at") or 0) < MIN_GAP:
                points[-1] = point
            else:
                points.append(point)
            data.pop(root, None)
            data[root] = points[-POINTS_PER_DISK:]  # Re-inserted last: the dict keeps recent disks last
            while len(data) > DISKS_LIMIT:
                data.pop(next(iter(data)))
            storage.write_json(LOG_FILE, data)
    except (OSError, ValueError, TypeError):
        pass


def record_paths(paths: Iterable[str]) -> None:
    """Note the free space of each disk the paths are on (after a cleanup)."""
    roots = set()
    for path in paths:
        try:
            roots.add(_root(path))
        except (OSError, ValueError):
            continue
        if len(roots) >= DISKS_LIMIT:
            break
    for root in roots:
        if os.path.isdir(root):
            record(root)


def history(cleanups: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """{"disks": [{"root", "points": [{"at", "total", "free"}]}], "cleanups": [{"at", "freed_bytes", "mode", "roots"}]}"""
    data = _load()
    disks = [{"root": root, "points": sorted((p for p in points if isinstance(p, dict)), key=lambda p: p.get("at", 0))}
             for root, points in data.items() if isinstance(points, list) and points]
    disks.reverse()  # Most recently used disk first
    out = []
    for record_ in storage.load_history() if cleanups is None else cleanups:
        if not isinstance(record_, dict):
            continue
        roots = set()
        for p in (record_.get("paths") or [])[:50]:
            try:
                roots.add(_root(p))
            except (OSError, ValueError, TypeError):
                continue
        out.append({"at": record_.get("timestamp"), "freed_bytes": record_.get("freed_bytes", 0),
                    "mode": record_.get("mode", "recycle_bin"), "roots": sorted(roots)})
    return {"disks": disks, "cleanups": out}
