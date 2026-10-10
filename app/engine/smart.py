"""Offline smart scoring: how confident JunkZero is that an item is junk, and why.

Each flagged item gets a 1-99 score built from plain rules (its risk level, category,
age and location) plus what the user did with similar items before: what they deleted,
and what they kept through earlier scans. Nothing leaves the machine.
"""
from __future__ import annotations
import ntpath
import os
import time
from pathlib import PurePath
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import (
    CAT_BROKEN_DOWNLOADS,
    CAT_DUPLICATES,
    CAT_EMPTY_FOLDERS,
    CAT_LEFTOVERS,
    CAT_TEMP_JUNK,
    RISK_CAUTION,
    RISK_SAFE,
)
from app.engine import storage

# Score bands shown to the user
SCORE_DELETE = 75   # At or above: confident it is junk ("Delete")
SCORE_REVIEW = 45   # At or above: worth a look ("Review"); below: "Keep"

KEPT_LIMIT = 20000         # Kept paths remembered
KEPT_MIN_GAP = 20 * 3600   # A path counts as kept again only once a day
DAY = 86400.0


def recommendation(score: int) -> str:
    if score >= SCORE_DELETE:
        return "Delete"
    if score >= SCORE_REVIEW:
        return "Review"
    return "Keep"


def _norm(path: str) -> str:
    return os.path.normcase(path)


def signature(category: str, name: str, is_dir: bool) -> str:
    """What makes two items "similar" for learning: same category and file type (or folder name)."""
    if is_dir:
        kind = "folder:" + name.lower()
    else:
        kind = PurePath(name.lower()).suffix or name.lower()
    return f"{category}|{kind}"


def _kind_label(sig: str) -> str:
    kind = sig.split("|", 1)[1]
    if kind.startswith("folder:"):
        return f"'{kind[7:]}' folders"
    return f"{kind} files" if kind.startswith(".") else f"'{kind}' files"


def _location(path: str) -> Optional[str]:
    parts = [p.lower() for p in ntpath.normpath(path).replace("\\", "/").split("/")[:-1]]
    if "downloads" in parts:
        return "Downloads"
    if any(p in {"temp", "tmp"} for p in parts):
        return "a temp folder"
    if "desktop" in parts:
        return "Desktop"
    return None


# ---------------------------------------------------------------- Learning store

def load_learning() -> Dict[str, Any]:
    data = storage.read_json("learning.json", {})
    if not isinstance(data, dict):
        data = {}
    for key in ("deleted", "kept_kinds", "kept_paths"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def _save_learning(data: Dict[str, Any]) -> None:
    kept = data["kept_paths"]
    if len(kept) > KEPT_LIMIT:
        # Forget the paths seen longest ago
        newest = sorted(kept.items(), key=lambda kv: kv[1][1] if isinstance(kv[1], list) else 0, reverse=True)
        data["kept_paths"] = dict(newest[:KEPT_LIMIT])
    storage.write_json("learning.json", data)


def learn_deleted(items: Iterable[Dict[str, Any]], failed_paths: Iterable[str] = ()) -> int:
    """Remember the kinds of items the user deleted from the results table."""
    failed = {_norm(p) for p in failed_paths}
    counted = 0
    with storage.lock():
        data = load_learning()
        for item in items:
            path, category, name = item.get("path"), item.get("category"), item.get("name")
            if not (isinstance(path, str) and isinstance(category, str) and isinstance(name, str)):
                continue
            if _norm(path) in failed:
                continue
            sig = signature(category, name, bool(item.get("is_directory")))
            data["deleted"][sig] = int(data["deleted"].get(sig, 0)) + 1
            data["kept_paths"].pop(_norm(path), None)
            counted += 1
        if counted:
            _save_learning(data)
    return counted


def learn_kept(items: Iterable[Any], previous_paths: Iterable[str], now: Optional[float] = None) -> int:
    """Items flagged by the previous scan and found again were kept by the user."""
    now = time.time() if now is None else now
    previous = {_norm(p) for p in previous_paths if isinstance(p, str)}
    counted = 0
    with storage.lock():
        data = load_learning()
        for item in items:
            key = _norm(item.path)
            if key not in previous:
                continue
            entry = data["kept_paths"].get(key)
            count, last = entry if isinstance(entry, list) and len(entry) == 2 else (0, 0.0)
            if now - last < KEPT_MIN_GAP:
                continue
            data["kept_paths"][key] = [count + 1, now]
            sig = signature(item.category, item.name, item.is_directory)
            data["kept_kinds"][sig] = int(data["kept_kinds"].get(sig, 0)) + 1
            counted += 1
        if counted:
            _save_learning(data)
    return counted


def forget_learning() -> None:
    with storage.lock():
        storage.write_json("learning.json", {"deleted": {}, "kept_kinds": {}, "kept_paths": {}})


def learning_summary() -> Dict[str, int]:
    data = load_learning()
    return {
        "deleted": sum(int(v) for v in data["deleted"].values()),
        "kept": len(data["kept_paths"]),
    }


# ---------------------------------------------------------------- Scoring

_RISK_BASE = {RISK_SAFE: 80, RISK_CAUTION: 20}
_REVIEW_BASE = 50


class SmartScorer:
    """Scores items; built once per scan from the learning store."""

    def __init__(self, learning: Optional[Dict[str, Any]] = None, now: Optional[float] = None):
        learning = learning or {}
        self.deleted = learning.get("deleted") or {}
        self.kept_kinds = learning.get("kept_kinds") or {}
        self.kept_paths = learning.get("kept_paths") or {}
        self.now = now

    def score(self, item: Any) -> Tuple[int, List[str]]:
        """Return (score, reasons) for a GarbageItem; reasons explain each adjustment."""
        now = time.time() if self.now is None else self.now
        score = _RISK_BASE.get(item.risk_level, _REVIEW_BASE)
        reasons: List[str] = [f"Starts at {score}: rated {item.risk_level}"]

        def adjust(points: int, why: str) -> None:
            nonlocal score
            score += points
            reasons.append(f"{'+' if points > 0 else ''}{points}: {why}")

        if item.category == CAT_BROKEN_DOWNLOADS:
            adjust(10, "unfinished downloads can't be opened")
        elif item.category == CAT_DUPLICATES:
            adjust(10, "an identical copy is kept")
        elif item.category == CAT_EMPTY_FOLDERS:
            adjust(-10, "some apps expect their empty folders to exist")
        elif item.category == CAT_LEFTOVERS:
            adjust(5, "the program that made it is no longer installed")

        summary = getattr(item, "archive_summary", "")
        if summary:
            if getattr(item, "archive_clean", False):
                adjust(15, f"looked inside: {summary}, nothing personal")
            else:
                adjust(5, f"looked inside: {summary}")

        if item.modified_timestamp > 0:
            age_days = (now - item.modified_timestamp) / DAY
            if age_days < 1:
                adjust(-20, "changed in the last day, may still be in use")
            elif age_days < 7:
                adjust(-8, "changed this week")
            elif age_days >= 365:
                adjust(10, f"untouched for {int(age_days // 365)} year{'s' if age_days >= 730 else ''}")
            elif age_days >= 180:
                adjust(5, f"untouched for {int(age_days // 30)} months")

        where = _location(item.path)
        if where and item.category != CAT_TEMP_JUNK:
            adjust(5, f"sits in {where}")

        sig = signature(item.category, item.name, item.is_directory)
        deleted = int(self.deleted.get(sig, 0))
        kept_kind = int(self.kept_kinds.get(sig, 0))
        if deleted >= 2:
            adjust(min(15, 3 * deleted), f"you deleted {deleted} {_kind_label(sig)} like this before")
        elif deleted == 0 and kept_kind >= 3:
            adjust(-10, f"you usually keep {_kind_label(sig)}")

        kept = self.kept_paths.get(_norm(item.path))
        times_kept = kept[0] if isinstance(kept, list) and kept else 0
        if times_kept >= 2:
            adjust(-25, f"you kept this through {times_kept} earlier scans")
        elif times_kept == 1:
            adjust(-10, "you kept this after the last scan")

        return max(1, min(99, score)), reasons

    def apply(self, item: Any) -> Any:
        item.score, item.score_reasons = self.score(item)
        item.recommendation = recommendation(item.score)
        return item
