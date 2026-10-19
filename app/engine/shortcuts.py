"""Start menu, desktop and applications-menu shortcuts for copies of JunkZero no installer set up.

The Windows installer (JunkZero-Setup.exe) adds a Start menu entry and, if ticked, a desktop
shortcut itself, and the Linux .deb adds a menu entry; both remove them on uninstall, so nothing
is offered for those copies. The portable downloads and a copy run from source have none, so
JunkZero offers to add them, for the current user only:
- Windows: "JunkZero.lnk" in the Start menu and on the desktop.
- Linux: an applications-menu entry (~/.local/share/applications/junkzero.desktop). No desktop
  icon: desktops treat launchers there differently (Xfce, GNOME and KDE each ask in their own
  way whether to trust them), so it couldn't be made to just work.
JunkZero records each shortcut it made, and when the program one starts is gone (the portable
folder was moved, or unpacked somewhere else), points it at the copy now running. Shortcuts it
didn't make are never changed or removed. A Mac app goes in Applications (the .dmg shows where),
so nothing is offered there.
"""
from __future__ import annotations
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.engine import osinfo, storage

logger = logging.getLogger("JunkZeroShortcuts")

REPO_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_NAME = "JunkZero.lnk"
LINUX_NAME = "junkzero.desktop"  # the same name as the .deb's entry, so the two never show twice
LINUX_INSTALL_DIR = Path("/opt/junkzero")  # where the .deb puts JunkZero
LINUX_SYSTEM_ENTRIES = (Path("/usr/share/applications"), Path("/usr/local/share/applications"))
PLACE_LABELS = {"start_menu": "Start menu", "desktop": "Desktop", "menu": "Applications menu"}
_KNOWN_FOLDERS = {  # Windows known-folder ids (they follow a Desktop moved to OneDrive)
    "start_menu": "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}",  # Start Menu\Programs
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
}
# Writes the shortcut described in JUNKZERO_LNK_* (paths never go into the script itself)
_LNK_SCRIPT = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:JUNKZERO_LNK_PATH);"
    "$s.TargetPath = $env:JUNKZERO_LNK_TARGET;"
    "$s.Arguments = $env:JUNKZERO_LNK_ARGS;"
    "$s.WorkingDirectory = $env:JUNKZERO_LNK_DIR;"
    "$s.IconLocation = $env:JUNKZERO_LNK_ICON;"
    "$s.Description = 'Find and remove junk files safely';"
    "$s.Save()"
)


class ShortcutError(Exception):
    pass


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def installed_by_installer() -> bool:
    """A copy the Windows installer or the Linux .deb put in place: they manage its shortcuts."""
    if not _frozen():
        return False
    exe = Path(sys.executable).resolve()
    if osinfo.is_windows():
        return (exe.parent / "unins000.exe").exists()
    return exe.parent == LINUX_INSTALL_DIR


def supported() -> bool:
    return (osinfo.is_windows() or osinfo.is_linux()) and not installed_by_installer()


def places() -> Tuple[str, ...]:
    if osinfo.is_windows():
        return ("start_menu", "desktop")
    if osinfo.is_linux():
        return ("menu",)
    return ()


def _icon_png() -> Optional[Path]:
    """JunkZero's icon for the Linux menu entry: bundled with the portable build, in packaging/
    when run from source."""
    base = Path(getattr(sys, "_MEIPASS", "")) if _frozen() else REPO_ROOT
    icon = base / "packaging" / "JunkZero.png"
    return icon if icon.is_file() else None


def launch_target() -> Tuple[str, List[str], str, str]:
    """(program, arguments, working folder, icon) that start this copy of JunkZero's window."""
    if _frozen():
        exe = str(Path(sys.executable).resolve())
        icon = exe if osinfo.is_windows() else str(_icon_png() or "")
        return exe, [], str(Path(exe).parent), icon
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")  # Windows: no console window behind JunkZero's own
    program = str(windowless if windowless.exists() else exe)
    icon = REPO_ROOT / "packaging" / ("JunkZero.ico" if osinfo.is_windows() else "JunkZero.png")
    return program, ["-m", "app.main", "--mode", "gui"], str(REPO_ROOT), str(icon) if icon.is_file() else ""


def _target_key() -> str:
    program, arguments, _, _ = launch_target()
    return " ".join([program] + arguments)


# ---------------------------------------------------------------- where shortcuts go

def _known_folder(place: str) -> Optional[Path]:
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        guid = GUID()
        ctypes.oledll.ole32.CLSIDFromString(_KNOWN_FOLDERS[place], ctypes.byref(guid))
        out = ctypes.c_wchar_p()
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        try:
            return Path(out.value) if out.value else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except (OSError, AttributeError, ValueError) as e:
        logger.warning(f"Could not find the {PLACE_LABELS[place]} folder: {e}")
        return None


def _linux_applications_dir() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME") or ""
    base = Path(data_home) if os.path.isabs(data_home) else Path.home() / ".local" / "share"
    return base / "applications"


def shortcut_path(place: str) -> Optional[Path]:
    if place == "menu":
        return _linux_applications_dir() / LINUX_NAME
    folder = _known_folder(place)
    return folder / WINDOWS_NAME if folder else None


def _present(place: str, path: Optional[Path]) -> bool:
    if path is not None and path.exists():
        return True
    # The .deb's own menu entry (JunkZero installed besides this copy) counts as one already there
    return place == "menu" and any((d / LINUX_NAME).exists() for d in LINUX_SYSTEM_ENTRIES)


# ---------------------------------------------------------------- writing them

def _desktop_exec_arg(arg: str) -> str:
    """One argument of a .desktop Exec line, quoted as the freedesktop spec says."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`=%'):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return '"' + escaped.replace("%", "%%") + '"'


def _desktop_value(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")


def desktop_entry() -> str:
    program, arguments, workdir, icon = launch_target()
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=JunkZero",
        "GenericName=Junk File Cleaner",
        "Comment=Find and remove junk files safely",
        "Exec=" + _desktop_value(" ".join(_desktop_exec_arg(a) for a in [program] + arguments)),
        "Path=" + _desktop_value(workdir),
    ]
    if icon:
        lines.append("Icon=" + _desktop_value(icon))
    lines += [
        "Terminal=false",
        "Categories=System;Utility;FileTools;",
        "Keywords=clean;junk;disk;space;cache;duplicates;",
        "StartupNotify=false",
    ]
    return "\n".join(lines) + "\n"


def _write_desktop_entry(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(desktop_entry(), encoding="utf-8")
        tmp.chmod(0o755)
        os.replace(tmp, path)
    except OSError as e:
        raise ShortcutError(f"Couldn't write {path}: {e.strerror or e}") from e
    # Menus pick up the new entry by themselves; this only makes it quicker where it's installed
    try:
        subprocess.run(["update-desktop-database", str(path.parent)], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def _write_lnk(path: Path) -> None:
    program, arguments, workdir, icon = launch_target()
    env = {**os.environ, "JUNKZERO_LNK_PATH": str(path), "JUNKZERO_LNK_TARGET": program,
           "JUNKZERO_LNK_ARGS": subprocess.list2cmdline(arguments), "JUNKZERO_LNK_DIR": workdir,
           "JUNKZERO_LNK_ICON": icon}
    command = ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", _LNK_SCRIPT]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        res = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as e:
        raise ShortcutError(f"Windows couldn't make the shortcut: {e}") from e
    if res.returncode != 0 or not path.exists():
        detail = (res.stderr or res.stdout or "").strip().splitlines()
        raise ShortcutError("Windows couldn't make the shortcut" + (f": {detail[-1]}" if detail else "."))


def _write_shortcut(place: str, path: Path) -> None:
    if place == "menu":
        _write_desktop_entry(path)
    else:
        _write_lnk(path)


# ---------------------------------------------------------------- what the app calls

def _made() -> Dict[str, Dict[str, str]]:
    saved = storage.load_settings().get("shortcuts")
    made = saved.get("made") if isinstance(saved, dict) else None
    return {k: v for k, v in made.items() if k in PLACE_LABELS and isinstance(v, dict)} if isinstance(made, dict) else {}


def _save(made: Optional[Dict[str, Dict[str, str]]] = None, offer_dismissed: Optional[bool] = None) -> None:
    with storage.lock():
        current = storage.load_settings().get("shortcuts")
        current = dict(current) if isinstance(current, dict) else {}
        if made is not None:
            current["made"] = made
        if offer_dismissed is not None:
            current["offer_dismissed"] = offer_dismissed
        storage.update_settings(shortcuts=current)


def status() -> Dict[str, Any]:
    """What the About screen and the first-launch offer show."""
    if not supported():
        return {"supported": False, "places": [], "offer": False}
    shown: List[Dict[str, Any]] = []
    for place in places():
        path = shortcut_path(place)
        shown.append({"id": place, "label": PLACE_LABELS[place], "available": path is not None,
                      "present": _present(place, path)})
    saved = storage.load_settings().get("shortcuts")
    dismissed = bool(saved.get("offer_dismissed")) if isinstance(saved, dict) else False
    offer = not dismissed and all(p["available"] and not p["present"] for p in shown)
    return {"supported": True, "places": shown, "offer": offer}


def add(wanted: List[str]) -> Dict[str, Any]:
    """Make a shortcut in each chosen place that has none yet. Raises ShortcutError naming what failed."""
    if not supported():
        raise ShortcutError("JunkZero makes shortcuts only for a portable or from-source copy on Windows or Linux.")
    chosen = [p for p in places() if p in wanted]
    if not chosen:
        raise ShortcutError("Choose where to add JunkZero.")
    made = _made()
    failed: List[str] = []
    for place in chosen:
        path = shortcut_path(place)
        if path is None:
            failed.append(f"{PLACE_LABELS[place]}: the system didn't say where that folder is.")
            continue
        if _present(place, path):
            continue  # one is there already (an installer's, or one made earlier): leave it as it is
        try:
            _write_shortcut(place, path)
            made[place] = {"path": str(path), "program": launch_target()[0], "target": _target_key()}
        except ShortcutError as e:
            failed.append(f"{PLACE_LABELS[place]}: {e}")
    _save(made=made, offer_dismissed=True)
    if failed:
        raise ShortcutError(" ".join(failed))
    return status()


def dismiss_offer() -> Dict[str, Any]:
    _save(offer_dismissed=True)
    return status()


def repair() -> int:
    """At startup: point shortcuts JunkZero made at this copy when the program they start is
    gone (the copy was moved or unpacked somewhere else). While that program is still there the
    shortcut is left alone, so two copies never take it from each other. Shortcuts the user
    deleted are forgotten, never made again. Returns how many were updated."""
    if not supported():
        return 0
    made = _made()
    if not made:
        return 0
    target = _target_key()
    updated = 0
    for place, record in list(made.items()):
        path = Path(record.get("path") or "")
        if not record.get("path") or not path.exists():
            del made[place]
            continue
        old_program = record.get("program") or ""
        if record.get("target") == target or (old_program and Path(old_program).exists()):
            continue
        try:
            _write_shortcut(place, path)
            record["program"], record["target"] = launch_target()[0], target
            updated += 1
        except ShortcutError as e:
            logger.warning(f"Could not update the {PLACE_LABELS[place]} shortcut: {e}")
    _save(made=made)
    return updated
