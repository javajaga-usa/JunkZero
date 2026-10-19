"""Caches of chat, music, game and editor apps (Teams, Slack, Discord, Zoom, VS Code, Steam).

These apps are built on Chromium and keep its standard cache folders (Cache, Code Cache,
GPUCache), which they rebuild on the next start. Only the exact folders below are offered,
each as one item, and only while that app is closed: if the list of running programs can't be
read, none of them is offered. Messages, settings, downloads and offline data live elsewhere
in the same app folders and are never touched.
"""
from __future__ import annotations
import csv
import io
import os
import subprocess
import time
from typing import Iterable, List, Mapping, Optional, Set, Tuple

from app.engine import osinfo

_CHROMIUM_CACHES = ["Cache", "Code Cache", "GPUCache"]

# (id, app name, base folder variable, folder inside it, cache subfolders or [] for the folder
# itself, program names that mean the app is running)
_WINDOWS_APP_CACHES: List[Tuple[str, str, str, str, List[str], Set[str]]] = [
    ("teams_classic", "Microsoft Teams (classic)", "APPDATA", "Microsoft/Teams", _CHROMIUM_CACHES, {"teams.exe"}),
    ("teams", "Microsoft Teams", "LOCALAPPDATA",
     "Packages/MSTeams_8wekyb3d8bbwe/LocalCache/Microsoft/MSTeams/EBWebView/Default", _CHROMIUM_CACHES,
     {"ms-teams.exe", "msteams.exe", "teams.exe"}),
    ("slack", "Slack", "APPDATA", "Slack", _CHROMIUM_CACHES, {"slack.exe"}),
    ("discord", "Discord", "APPDATA", "discord", _CHROMIUM_CACHES, {"discord.exe"}),
    ("zoom", "Zoom", "APPDATA", "Zoom/logs", [], {"zoom.exe"}),
    ("vscode", "VS Code", "APPDATA", "Code", _CHROMIUM_CACHES + ["CachedData", "CachedExtensionVSIXs"], {"code.exe"}),
    ("steam", "Steam", "LOCALAPPDATA", "Steam/htmlcache", [], {"steam.exe", "steamwebhelper.exe"}),
]

# The same on a Mac, inside ~/Library/Application Support. (Spotify, Zoom logs and most other
# caches already sit in ~/Library/Caches and ~/Library/Logs, which Quick Clean covers.)
_MAC_APP_CACHES: List[Tuple[str, str, str, List[str], Set[str]]] = [
    ("teams_classic", "Microsoft Teams (classic)", "Microsoft/Teams", _CHROMIUM_CACHES, {"microsoft teams", "teams", "msteams"}),
    ("slack", "Slack", "Slack", _CHROMIUM_CACHES, {"slack"}),
    ("discord", "Discord", "discord", _CHROMIUM_CACHES, {"discord"}),
    # VS Code's own program is called Electron, like other Electron apps: any of them running counts
    ("vscode", "VS Code", "Code", _CHROMIUM_CACHES + ["CachedData", "CachedExtensionVSIXs"], {"code", "electron"}),
]


# The last list of running programs and when it was read: a scan asks for it more than once
_running_cache: Tuple[float, Optional[Set[str]]] = (0.0, None)
_RUNNING_CACHE_SECONDS = 10


def running_programs() -> Optional[Set[str]]:
    """Lowercase names of the programs running now, or None if they can't be listed."""
    global _running_cache
    when, programs = _running_cache
    if programs is not None and time.monotonic() - when < _RUNNING_CACHE_SECONDS:
        return programs
    programs = _list_running()
    _running_cache = (time.monotonic(), programs)
    return programs


def _list_running() -> Optional[Set[str]]:
    try:
        if osinfo.is_windows():
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            res = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                                 timeout=20, creationflags=flags)
            if res.returncode != 0:
                return None
            return {row[0].strip().lower() for row in csv.reader(io.StringIO(res.stdout)) if row}
        res = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=20)
        if res.returncode != 0:
            return None
        return {os.path.basename(line.strip()).lower() for line in res.stdout.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _folders(base: str, rel: str, subdirs: List[str]) -> List[Tuple[str, str]]:
    """(cache folder, its name) for each existing subfolder, or the folder itself."""
    folder = os.path.normpath(os.path.join(base, *rel.split("/")))
    if not subdirs:
        return [(folder, os.path.basename(folder))]
    return [(os.path.join(folder, sub), sub) for sub in subdirs]


def app_cache_entries(env: Optional[Mapping[str, str]] = None, running: Optional[Iterable[str]] = ()
                      ) -> List[Tuple[str, str, str]]:
    """(id, label, path) of the app caches of closed apps that exist on this machine. running:
    the running programs (None: they couldn't be listed, so nothing is offered)."""
    if running is None:
        return []
    env = os.environ if env is None else env
    running = {r.lower() for r in running}
    found: List[Tuple[str, str, str]] = []
    if osinfo.is_macos():
        home = env.get("HOME")
        if not home:
            return []
        support = os.path.join(home, "Library", "Application Support")
        entries = [(i, name, support, rel, subs, programs) for i, name, rel, subs, programs in _MAC_APP_CACHES]
    else:
        entries = [(i, name, env.get(var) or "", rel, subs, programs)
                   for i, name, var, rel, subs, programs in _WINDOWS_APP_CACHES]
    for app_id, name, base, rel, subdirs, programs in entries:
        if not base or programs & running:
            continue
        for path, sub in _folders(base, rel, subdirs):
            if os.path.isdir(path) and not os.path.islink(path):
                found.append((f"app:{app_id}:{sub}".lower().replace(" ", "_"), f"{name} cache ({sub})", path))
    return found
