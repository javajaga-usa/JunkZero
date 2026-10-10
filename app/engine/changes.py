"""Compare a completed scan with the previous scan of the same target ("new since last scan")."""
from __future__ import annotations
import os
from typing import Any, Dict, Iterable, List

from app.config import CATEGORY_OPTIONS, ScanOptions
from app.engine import smart, storage
from app.engine.classifier import GarbageItem

QUICK_CLEAN_KEY = "quick-clean"


def scan_key(options: ScanOptions) -> str:
    """Scans are compared per target folder; Quick Clean (no target) has its own slot."""
    if not options.target_path:
        return QUICK_CLEAN_KEY
    key = os.path.normcase(os.path.abspath(options.target_path))
    return key + " +junk-locations" if options.scan_junk_locations else key


def enabled_categories(options: ScanOptions) -> List[str]:
    return [cat for cat, flag in CATEGORY_OPTIONS.items() if getattr(options, flag, False)]


def compare_and_remember(options: ScanOptions, items: Iterable[GarbageItem]) -> Dict[str, Any]:
    """Return the paths not flagged by the previous scan, then remember this scan.

    Only categories that the previous scan also looked for count as new, so turning
    on a category does not mark everything in it as new. The first scan of a target
    has nothing to compare with (previous_scan_at is None).
    """
    items = list(items)
    key = scan_key(options)
    previous = storage.last_scan(key)
    if previous and not previous.get("complete", True):
        previous = None
    new_paths: List[str] = []
    if previous:
        seen = {os.path.normcase(p) for p in previous["paths"] if isinstance(p, str)}
        compared = set(previous.get("categories") or [])
        new_paths = [
            i.path for i in items
            if i.category in compared and os.path.normcase(i.path) not in seen
        ]
        # Found again after the last scan: the user chose to keep it (feeds the smart score)
        smart.learn_kept(items, previous["paths"])
    storage.remember_scan(key, [i.path for i in items], enabled_categories(options))
    return {
        "previous_scan_at": previous.get("at") if previous else None,
        "new_paths": new_paths,
    }
