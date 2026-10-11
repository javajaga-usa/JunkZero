"""What a delete is about to remove, shown in the confirm dialog before anything is deleted.

Unusually large deletes (over LARGE_DELETE_BYTES or LARGE_DELETE_ITEMS) and any delete that
includes personal files need the user to type DELETE; the server checks this too.
"""
from __future__ import annotations
import os
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import LARGE_DELETE_BYTES, LARGE_DELETE_ITEMS
from app.engine import recycle
from app.engine.classifier import format_size
from app.engine.safeguards import cloud_provider, is_personal_file

CONFIRM_WORD = "DELETE"
# Files looked at inside folders chosen in Folder Explorer before giving up (and asking to type DELETE)
WALK_LIMIT = 200_000


def _walk(path: str, limit: int):
    """(file path, size) for every file inside path, and whether the listing stopped early."""
    seen = 0
    files = []
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in names:
            fp = os.path.join(root, name)
            try:
                size = os.lstat(fp).st_size
            except OSError:
                size = 0
            files.append((fp, size))
            seen += 1
            if seen >= limit:
                return files, True
    return files, False


def summarize(
    entries: Iterable[Dict[str, Any]],
    permanent: bool,
    cloud: Iterable[Tuple[str, str]] = (),
    look_inside: bool = False,
    walk_limit: int = WALK_LIMIT,
) -> Dict[str, Any]:
    """Summary of entries ({path, size_bytes, is_directory}). look_inside=True counts the files
    inside folders (Folder Explorer); scan results are counted as the items listed, except
    folders marked look_inside (ones the scan was not sure about)."""
    cloud = list(cloud)
    count = 0
    total = 0
    personal: List[str] = []
    types: Counter = Counter()
    folders: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    providers: Counter = Counter()
    no_recycle = 0
    truncated = False
    recycle_cache: Dict[str, bool] = {}

    def has_bin(path: str) -> bool:
        root = recycle.mount_point(path)
        if root not in recycle_cache:
            recycle_cache[root] = recycle.has_recycle_bin(path)
        return recycle_cache[root]

    def add_file(path: str, size: int, group: str) -> None:
        nonlocal count, total
        count += 1
        total += size
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        types[ext or "(no extension)"] += 1
        if is_personal_file(name):
            personal.append(name)
        folders[group][0] += 1
        folders[group][1] += size

    for entry in entries:
        path = str(entry.get("path") or "")
        if not path:
            continue
        is_dir = bool(entry.get("is_directory")) or os.path.isdir(path)
        provider = entry.get("cloud_provider") or cloud_provider(path, cloud)
        if provider:
            providers[provider] += 1
        if not permanent and os.path.lexists(path) and not has_bin(path):
            no_recycle += 1
        if is_dir and (look_inside or entry.get("look_inside")):
            files, stopped = _walk(path, max(1, walk_limit - count))
            truncated = truncated or stopped
            for fp, size in files:
                add_file(fp, size, path)
            continue
        size = entry.get("size_bytes")
        size = size if isinstance(size, int) and size >= 0 else 0
        parent = os.path.dirname(path.rstrip("\\/"))
        if is_dir:
            count += 1
            total += size
            types["folders"] += 1
            folders[parent][0] += 1
            folders[parent][1] += size
        else:
            add_file(path, size, parent)
        if entry.get("personal") and not is_dir and os.path.basename(path) not in personal:
            personal.append(os.path.basename(path))

    top = sorted(folders.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))[:5]
    return {
        "count": count,
        "bytes": total,
        "bytes_formatted": format_size(total),
        "personal_count": len(personal),
        "personal_examples": personal[:3],
        "cloud": dict(providers),
        "no_recycle_count": no_recycle,
        "top_folders": [{"folder": f, "count": c, "bytes_formatted": format_size(b)} for f, (c, b) in top],
        "types": [{"type": t, "count": c} for t, c in types.most_common(6)],
        "truncated": truncated,
        "needs_typed_confirm": (
            truncated or total > LARGE_DELETE_BYTES or count > LARGE_DELETE_ITEMS or bool(personal)
        ),
    }


def confirmed(summary: Dict[str, Any], confirm_text: Optional[str]) -> bool:
    return not summary["needs_typed_confirm"] or (confirm_text or "").strip().upper() == CONFIRM_WORD
