"""Safeguards for personal files, applied to every flagged item after it is scored.

Some items are never preselected and never marked "Delete" by the smart score, whatever
their category, rating or what the user deleted before:
  * personal file types (documents, photos, videos, audio, game saves),
  * files changed in the last 24 hours (they may still be in use),
  * anything inside a cloud-sync folder (OneDrive, Dropbox, Google Drive, iCloud Drive, Box),
    because deleting it there also deletes the cloud copy and the copy on other devices.
The user can still tick them by hand.
"""
from __future__ import annotations
import json
import os
import string
import time
from typing import Any, Iterable, List, Mapping, Optional, Tuple

from app.config import RISK_REVIEW, RISK_SAFE
from app.engine import osinfo
from app.engine.archives import PERSONAL_EXTENSIONS
from app.engine.smart import SCORE_DELETE, recommendation

# Files people make and keep, plus screenshots and game saves
PERSONAL_FILE_EXTENSIONS = PERSONAL_EXTENSIONS | {
    ".png", ".gif", ".bmp", ".sav", ".save", ".savegame", ".sl2", ".ess",
}
# System clutter that shares an extension with personal files (Thumbs.db is a .db)
_NOT_PERSONAL_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}

RECENT_SECONDS = 24 * 3600


def is_personal_file(name: str) -> bool:
    lower = name.lower()
    if lower in _NOT_PERSONAL_NAMES:
        return False
    return os.path.splitext(lower)[1] in PERSONAL_FILE_EXTENSIONS


# ---------------------------------------------------------------- Cloud-sync folders

# Folder name prefixes directly in the home folder (Windows and macOS)
_HOME_CLOUD_PREFIXES = (
    ("onedrive", "OneDrive"), ("dropbox", "Dropbox"), ("google drive", "Google Drive"),
    ("my drive", "Google Drive"), ("icloud drive", "iCloud Drive"), ("iclouddrive", "iCloud Drive"),
    ("box", "Box"), ("pcloud", "pCloud"), ("mega", "MEGA"), ("nextcloud", "Nextcloud"),
)
# macOS File Provider folders in ~/Library/CloudStorage ("OneDrive-Personal", "GoogleDrive-me@x.com")
_MAC_CLOUD_PREFIXES = (
    ("onedrive", "OneDrive"), ("dropbox", "Dropbox"), ("googledrive", "Google Drive"), ("box", "Box"),
)


def _provider_for(name: str, prefixes) -> Optional[str]:
    lower = name.lower()
    for prefix, label in prefixes:
        # "Box" and "MEGA" only as the whole name or "Box Sync", not "Boxes" or "Megadeth"
        if lower == prefix or lower.startswith(prefix + " ") or lower.startswith(prefix + "-") \
                or (len(prefix) > 4 and lower.startswith(prefix)):
            return label
    return None


def _dropbox_paths(env: Mapping[str, str], home: Optional[str]) -> List[str]:
    """Dropbox folders from Dropbox's own info.json (it can be moved anywhere)."""
    bases = [env.get("APPDATA"), env.get("LOCALAPPDATA"), os.path.join(home, ".dropbox") if home else None]
    paths = []
    for base in bases:
        if not base:
            continue
        for candidate in (os.path.join(base, "Dropbox", "info.json"), os.path.join(base, "info.json")):
            try:
                with open(candidate, encoding="utf-8") as f:
                    info = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(info, dict):
                for account in info.values():
                    if isinstance(account, dict) and isinstance(account.get("path"), str):
                        paths.append(account["path"])
    return paths


def cloud_folders(env: Optional[Mapping[str, str]] = None) -> List[Tuple[str, str]]:
    """(folder, provider name) for every cloud-sync folder found for the current user."""
    env = os.environ if env is None else env
    home = env.get("USERPROFILE") or env.get("HOME")
    found: List[Tuple[str, str]] = []
    seen = set()

    def add(path: Optional[str], label: str) -> None:
        if not path or not os.path.isdir(path):
            return
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            found.append((os.path.abspath(path), label))

    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        add(env.get(key), "OneDrive")
    for path in _dropbox_paths(env, home):
        add(path, "Dropbox")
    if home:
        try:
            entries = list(os.scandir(home))
        except OSError:
            entries = []
        for entry in entries:
            label = _provider_for(entry.name, _HOME_CLOUD_PREFIXES)
            if label:
                add(entry.path, label)
        storage = os.path.join(home, "Library", "CloudStorage")
        try:
            entries = list(os.scandir(storage))
        except OSError:
            entries = []
        for entry in entries:
            add(entry.path, _provider_for(entry.name, _MAC_CLOUD_PREFIXES) or "a cloud-sync folder")
        add(os.path.join(home, "Library", "Mobile Documents"), "iCloud Drive")
    if osinfo.is_windows():
        # Google Drive for desktop shows up as its own drive letter holding "My Drive"
        for letter in string.ascii_uppercase[3:]:
            add(f"{letter}:\\My Drive", "Google Drive")
    return found


def _norm(path: str) -> str:
    norm = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
    return norm.lower() if osinfo.is_macos() else norm


def cloud_provider(path: str, folders: Iterable[Tuple[str, str]]) -> str:
    """The provider name if path is inside (or is) a cloud-sync folder, else ""."""
    norm = _norm(path)
    for folder, label in folders:
        root = _norm(folder)
        if norm == root or norm.startswith(root + os.sep):
            return label
    return ""


# ---------------------------------------------------------------- Applying them

class Safeguards:
    """Built once per scan; apply() runs on every item after its smart score."""

    def __init__(self, cloud: Optional[List[Tuple[str, str]]] = None, now: Optional[float] = None):
        self.cloud = cloud_folders() if cloud is None else cloud
        self.now = now

    def apply(self, item: Any) -> Any:
        now = time.time() if self.now is None else self.now
        notes: List[str] = []

        provider = cloud_provider(item.path, self.cloud)
        if provider:
            item.cloud_provider = provider
            item.reason += f"; in {provider}: deleting it also removes it from the cloud and your other devices"
            notes.append(f"it is in {provider}")
        if not item.is_directory and is_personal_file(item.name):
            item.personal = True
            notes.append("it looks like a personal file (document, photo, video or save)")
        if not item.is_directory and item.modified_timestamp > 0 and now - item.modified_timestamp < RECENT_SECONDS:
            notes.append("it changed in the last 24 hours")

        if notes:
            item.selected = False
            if item.risk_level == RISK_SAFE:
                item.risk_level = RISK_REVIEW
            if item.score >= SCORE_DELETE:
                item.score = SCORE_DELETE - 1
            item.score_reasons = list(item.score_reasons) + ["Never marked Delete: " + "; ".join(notes)]
            item.recommendation = recommendation(item.score)
        return item
