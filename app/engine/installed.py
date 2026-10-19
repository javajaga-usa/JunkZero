"""Is the program a setup file installs already on this computer, at the same or a newer version?

The installer's own version info names its product and version ("7-Zip", "23.01"); Windows'
list of installed programs names theirs ("7-Zip 23.01 (x64)", "23.01"). The names must be the
same once versions, "(x64)" and the like are taken out; anything less certain is no match.
"""
from __future__ import annotations
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

from app.engine import osinfo

# Words left out of program names before comparing: architecture, "setup" and the like
_NOISE_WORDS = {
    "x64", "x86", "x8664", "amd64", "arm64", "win64", "win32", "64bit", "32bit", "bit", "setup", "installer",
    "install", "version", "edition",
}
_CACHE_SECONDS = 600
_cache: Tuple[float, Optional[Dict[str, List[Tuple[str, str]]]]] = (0.0, None)
_cache_lock = threading.Lock()


def normalize(name: str) -> str:
    """"7-Zip 23.01 (x64)" -> "7zip"; "Mozilla Firefox (x64 en-US)" -> "mozillafirefox"."""
    text = re.sub(r"\([^)]*\)", " ", (name or "").lower())
    words = []
    for word in re.split(r"[\s_]+", text):
        if re.fullmatch(r"v?\d+(?:[.\-]\d+)*[a-z]?", word):
            continue  # A version number
        word = re.sub(r"[^a-z0-9+#]", "", word)
        if word and word not in _NOISE_WORDS:
            words.append(word)
    return "".join(words)


def version_tuple(text: str) -> Tuple[int, ...]:
    numbers = re.findall(r"\d+", text or "")[:4]
    parts = [int(n) for n in numbers]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()  # 23.01 and 23.01.0.0 are the same version
    return tuple(parts)


def _registry_programs() -> List[Tuple[str, str]]:
    """(DisplayName, DisplayVersion) of every program in the Windows uninstall list."""
    try:
        import winreg
    except ImportError:
        return []
    from app.engine.leftovers import _UNINSTALL_KEYS
    found: List[Tuple[str, str]] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key_path in _UNINSTALL_KEYS:
            try:
                key = winreg.OpenKey(hive, key_path)
            except OSError:
                continue
            with key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                            name = winreg.QueryValueEx(sub, "DisplayName")[0]
                            version = winreg.QueryValueEx(sub, "DisplayVersion")[0]
                    except OSError:
                        continue
                    if isinstance(name, str) and isinstance(version, str) and name.strip() and version.strip():
                        found.append((name.strip(), version.strip()))
    return found


def build_table(programs: List[Tuple[str, str]]) -> Dict[str, List[Tuple[str, str]]]:
    table: Dict[str, List[Tuple[str, str]]] = {}
    for name, version in programs:
        key = normalize(name)
        if len(key) >= 3 and version_tuple(version):
            table.setdefault(key, []).append((name, version))
    return table


def installed_table() -> Dict[str, List[Tuple[str, str]]]:
    """Installed programs by normalized name (read once every few minutes). Empty off Windows."""
    global _cache
    with _cache_lock:
        when, table = _cache
        if table is None or time.monotonic() - when > _CACHE_SECONDS:
            table = build_table(_registry_programs()) if osinfo.is_windows() else {}
            _cache = (time.monotonic(), table)
        return table


def already_installed(product: str, version: str,
                      table: Optional[Dict[str, List[Tuple[str, str]]]] = None) -> str:
    """A plain sentence when the product is installed at this version or a newer one, else ""."""
    key = normalize(product)
    wanted = version_tuple(version)
    if len(key) < 3 or not wanted:
        return ""
    entries = (installed_table() if table is None else table).get(key) or []
    same = [(n, v) for n, v in entries if version_tuple(v) == wanted]
    if same:
        return f"{_named(*same[0])} is already installed"
    newer = [(n, v) for n, v in entries if version_tuple(v) > wanted]
    if newer:
        return f"a newer version is already installed: {_named(*max(newer, key=lambda e: version_tuple(e[1])))}"
    return ""


def _named(name: str, version: str) -> str:
    # "Python 3.13.0 (64-bit)" already says its version
    return name if version in name or re.search(r"\d+\.\d+", name) else f"{name} {version}"
