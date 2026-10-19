"""Linux (MX Linux, Debian, Ubuntu and other freedesktop.org desktops): junk locations, protected
folders, disks, the folder picker, the file manager and the Trash.

Every rule here names exact, well-known locations. Hidden folders in the home folder hold
settings, keys, mail and browser profiles, so none of them is scanned or deleted except the
freedesktop.org cache folder (~/.cache) and npm's download cache.
"""
from __future__ import annotations
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Callable, Dict, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------- Personal folders

# freedesktop.org user folders (~/.config/user-dirs.dirs), with their usual names when the file
# doesn't say. On a localized system they can be called "Téléchargements", "Bilder" and so on.
XDG_USER_DIR_KEYS = {
    "DESKTOP": "Desktop", "DOCUMENTS": "Documents", "DOWNLOAD": "Downloads", "MUSIC": "Music",
    "PICTURES": "Pictures", "VIDEOS": "Videos", "PUBLICSHARE": "Public", "TEMPLATES": "Templates",
}

# Hidden folders in the home folder that are never deleted whole (what's inside .cache can be)
HOME_APP_FOLDERS = (".cache", ".config", ".local", ".local/share", ".local/state", ".npm", ".mozilla", ".var")


def config_home(env: Mapping[str, str]) -> str:
    return env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")


def xdg_user_dirs(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """KEY (DOWNLOAD, PICTURES...) -> folder, from ~/.config/user-dirs.dirs, else the usual names.
    A folder set to the home folder itself means "not used" and is left out."""
    env = os.environ if env is None else env
    home = env.get("HOME")
    if not home:
        return {}
    found = {key: os.path.join(home, name) for key, name in XDG_USER_DIR_KEYS.items()}
    try:
        with open(os.path.join(config_home(env), "user-dirs.dirs"), encoding="utf-8") as f:
            text = f.read(65536)
    except (OSError, UnicodeDecodeError):
        return found
    for m in re.finditer(r'^\s*XDG_([A-Z]+)_DIR\s*=\s*"([^"]*)"', text, re.M):
        key, value = m.group(1), m.group(2)
        if value.startswith("$HOME"):
            value = home + value[len("$HOME"):]
        elif not value.startswith("/"):
            continue  # the spec allows only "$HOME/..." or an absolute path
        value = os.path.normpath(value)
        if os.path.normpath(home) == value:
            found.pop(key, None)
        else:
            found[key] = value
    return found


# ---------------------------------------------------------------- System folders

# Top-level folders that belong to the system (paths starting with "/"). Nothing under them is
# scanned or deleted, except what LINUX_SYSTEM_ALLOWED lists. /home, /media and /mnt hold the
# user's own files and disks.
LINUX_SYSTEM_ROOTS = {
    "bin", "boot", "dev", "etc", "lib", "lib32", "lib64", "libx32", "lost+found", "opt", "proc",
    "run", "sbin", "snap", "srv", "sys", "usr", "var", "selinux", "live", "timeshift",
}
# Shared temp folders (each user can only remove their own files there) and where Fedora, Arch
# and others mount USB drives
LINUX_SYSTEM_ALLOWED = ["var/tmp", "run/media"]

# Inside the home folder, the only hidden folders looked at (and the folders leading to them)
HOME_HIDDEN_ALLOWED = [".cache", ".npm/_cacache"]


def _under(rel: List[str], allowed: str) -> bool:
    a = allowed.split("/")
    return rel[:len(a)] == a


def _leads_to(rel: List[str], allowed: str) -> bool:
    a = allowed.split("/")
    return len(rel) <= len(a) and a[:len(rel)] == rel


def _home_parts(rel: List[str]) -> Optional[List[str]]:
    """The parts after the home folder if rel is in /home/<name> or in the current home folder."""
    home = [p for p in os.environ.get("HOME", "").lower().split("/") if p]
    if home and len(rel) > len(home) and rel[:len(home)] == home:
        return rel[len(home):]
    if len(rel) >= 3 and rel[0] == "home":
        return rel[2:]
    if len(rel) >= 2 and rel[0] == "root":  # the administrator's home folder
        return rel[1:]
    return None


def is_protected(rel: List[str]) -> bool:
    """Linux rules for an absolute path, given as its lowercase parts after the leading "/"."""
    rel = [p for p in rel if p]
    if not rel:
        return False
    if rel[0] in LINUX_SYSTEM_ROOTS and not any(_under(rel, a) for a in LINUX_SYSTEM_ALLOWED):
        return True
    # The Trash on every disk (.Trash-1000, .Trash/1000) and in the home folder
    if any(p == ".trash" or p.startswith(".trash-") for p in rel):
        return True
    # MX Snapshot keeps its system copies (snapshot-*.iso) in /home/snapshot
    if rel[:2] == ["home", "snapshot"]:
        return True
    inside = _home_parts(rel)
    if inside and inside[0].startswith("."):
        return not any(_under(inside, a) or _leads_to(inside, a) for a in HOME_HIDDEN_ALLOWED)
    return False


def protected_folders(env: Mapping[str, str]) -> List[str]:
    """Folders on Linux that are never deleted whole: the user folders and the app-data folders."""
    home = env.get("HOME")
    if not home:
        return []
    folders = list(xdg_user_dirs(env).values())
    folders += [os.path.join(home, *name.split("/")) for name in HOME_APP_FOLDERS]
    folders.append(config_home(env))
    if env.get("XDG_CACHE_HOME"):
        folders.append(env["XDG_CACHE_HOME"])
    return folders


# ---------------------------------------------------------------- Junk locations

# Folders in ~/.cache that hold only what an app downloads or builds again by itself:
# (folder name, label). Other folders there are not listed, because some apps keep things
# there they can't rebuild (Xfce's saved sessions, offline music, clipboard history).
CACHE_FOLDERS = [
    ("thumbnails", "Thumbnail cache"),
    ("google-chrome", "Google Chrome cache"),
    ("chromium", "Chromium cache"),
    ("BraveSoftware", "Brave cache"),
    ("vivaldi", "Vivaldi cache"),
    ("microsoft-edge", "Microsoft Edge cache"),
    ("opera", "Opera cache"),
    ("mozilla", "Firefox cache"),
    ("thunderbird", "Thunderbird cache"),
    ("pip", "Python pip download cache"),
    ("yarn", "Yarn package cache"),
    ("go-build", "Go build cache"),
    ("mesa_shader_cache", "Mesa graphics shader cache"),
    ("mesa_shader_cache_db", "Mesa graphics shader cache"),
    ("nvidia", "NVIDIA shader cache"),
]
# (id, label, path relative to the home folder)
HOME_CACHES = [
    ("npm_cache", "npm download cache", ".npm/_cacache"),
]


def junk_locations(env: Optional[Mapping[str, str]], make: Callable[..., object],
                   protected: Callable[[str], bool]) -> List[object]:
    """Junk locations for the current Linux user. make builds a JunkLocation(id, label, path,
    whole_dir); protected is the safety filter (is_system_protected_path)."""
    env = os.environ if env is None else env
    home = env.get("HOME")
    if not home:
        return []
    cache = env.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
    found: List[object] = []
    seen = set()

    def add(loc_id: str, label: str, path: str) -> None:
        path = os.path.normpath(path)
        if path in seen or not os.path.isdir(path) or os.path.islink(path) or protected(path):
            return
        seen.add(path)
        found.append(make(loc_id, label, path, whole_dir=True))

    for name, label in CACHE_FOLDERS:
        add(f"linux_cache:{name}".lower(), label, os.path.join(cache, name))
    for loc_id, label, rel in HOME_CACHES:
        add(loc_id, label, os.path.join(home, *rel.split("/")))
    return found


# Junk only an administrator can remove: shown read-only with the command that clears it.
# (name, folder, file name pattern or None for everything, command)
SYSTEM_CLEANUP_HINTS = [
    ("Downloaded software packages (APT cache)", "/var/cache/apt/archives", re.compile(r".*\.deb$"),
     "sudo apt clean"),
    ("Old system logs (journal)", "/var/log/journal", None, "sudo journalctl --vacuum-time=2weeks"),
]
_HINT_SIZE_LIMIT = 200000


def _files_size(folder: str, pattern, limit: int) -> Optional[int]:
    total, seen = 0, 0
    try:
        for root, dirs, files in os.walk(folder, onerror=_raise):
            for name in files:
                if pattern is not None and not pattern.match(name):
                    continue
                seen += 1
                if seen > limit:
                    return None
                total += os.lstat(os.path.join(root, name)).st_size
    except OSError:
        return None
    return total


def _raise(error: OSError) -> None:
    raise error


def system_cleanup_hints(root: str = "/", size_limit: int = _HINT_SIZE_LIMIT) -> List[Dict[str, object]]:
    """[{name, path, size_bytes or None, fix}] for system junk that is there (anything left is
    at least 1 MB, or can't be measured: the journal is readable only by administrators)."""
    hints = []
    for name, folder, pattern, fix in SYSTEM_CLEANUP_HINTS:
        path = os.path.join(root, folder.lstrip("/"))
        if not os.path.isdir(path):
            continue
        size = _files_size(path, pattern, size_limit)
        if size is not None and size < 1024 * 1024:
            continue
        hints.append({"name": name, "path": path, "size_bytes": size, "fix": fix})
    return hints


# ---------------------------------------------------------------- Disks

# Network file systems: no Trash, and a delete there is gone for everyone
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "sshfs", "fuse.davfs2", "davfs",
              "fuse.rclone", "9p", "afs", "ceph", "fuse.gvfsd-fuse", "glusterfs", "fuse.glusterfs"}
# Linux file systems whose Trash folders work like the home folder's
LOCAL_FS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "zfs", "jfs", "reiserfs", "bcachefs"}
# Where desktops mount USB drives and memory cards
REMOVABLE_PREFIXES = ("/media/", "/run/media/")

_mounts_cache: Tuple[float, Dict[str, str]] = (0.0, {})


def _unescape_mount(text: str) -> str:
    """/proc/mounts writes spaces, tabs and backslashes in paths as octal escapes (\\040)."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def parse_mounts(text: str) -> Dict[str, str]:
    """Mount point -> file system type, from the text of /proc/self/mounts."""
    mounts = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 3:
            mounts[_unescape_mount(fields[1])] = fields[2]
    return mounts


def mounts(path: str = "/proc/self/mounts") -> Dict[str, str]:
    """Mount point -> file system type (read again after a minute)."""
    global _mounts_cache
    when, cached = _mounts_cache
    if time.time() - when < 60:
        return cached
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            cached = parse_mounts(f.read())
    except OSError:
        cached = {}
    _mounts_cache = (time.time(), cached)
    return cached


def _same_disk(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def is_removable_mount(root: str) -> bool:
    return (root.rstrip("/") + "/").startswith(REMOVABLE_PREFIXES)


def has_trash(path: str, root: str, fs: str) -> bool:
    """True if moving path to the Trash keeps it there. The home folder's disk and Linux-formatted
    internal disks keep a Trash; network shares don't, and on USB drives, memory cards and
    Windows-formatted partitions JunkZero uses its own holding folder instead."""
    fs = (fs or "").lower()
    if fs in NETWORK_FS:
        return False
    home = os.path.expanduser("~")
    if _same_disk(path if os.path.lexists(path) else root, home):
        return True
    if is_removable_mount(root):
        return False
    return fs in LOCAL_FS and trash_usable(root)


def trash_usable(root: str) -> bool:
    """True if the Trash can keep items from this disk: its top folder has this user's Trash
    (.Trash-<uid>, or .Trash/<uid>) or lets the user make one. A disk formatted on another
    computer often has a top folder only root can change, where the Trash can't be made."""
    uid = os.getuid() if hasattr(os, "getuid") else 0
    for trash in (os.path.join(root, f".Trash-{uid}"), os.path.join(root, ".Trash", str(uid))):
        if os.path.isdir(trash) and os.access(trash, os.W_OK | os.X_OK):
            return True
    return os.access(root, os.W_OK | os.X_OK)


def holding_reason(root: str, fs: str, label: str) -> str:
    """Why items on this disk go to JunkZero's holding folder instead of the Trash."""
    fs = (fs or "").lower()
    if fs in NETWORK_FS:
        return "network drives have no Trash"
    if is_removable_mount(root):
        return "on USB drives and memory cards the Trash is easy to lose, so JunkZero keeps items itself"
    if fs in LOCAL_FS:
        return "the top folder of this disk belongs to another account, so the Trash can't be used there"
    if fs:
        return f"on {label} disks JunkZero doesn't rely on the Trash"
    return "JunkZero can't tell how this disk is formatted, so it doesn't rely on the Trash"


def drives(info: Callable[[str, str], Dict[str, object]], env: Optional[Mapping[str, str]] = None,
           mount_table: Optional[Dict[str, str]] = None) -> List[Dict[str, object]]:
    """The home folder, the whole file system and mounted USB drives and other disks
    (/media/<user>/..., /run/media/<user>/..., /mnt/...). info(path, label) measures one."""
    env = os.environ if env is None else env
    found: List[Dict[str, object]] = []
    home = env.get("HOME")
    for path, label in ((home, "Home"), ("/", "File System")):
        if path and os.path.isdir(path):
            try:
                found.append(info(path, label))
            except OSError:
                continue
    table = mounts() if mount_table is None else mount_table
    for point in sorted(table):
        parent = os.path.dirname(point.rstrip("/"))
        under_media = point.startswith(REMOVABLE_PREFIXES) and os.path.dirname(parent) in ("/media", "/run/media")
        if not (under_media or parent == "/mnt" or (parent == "/media" and point.count("/") == 2)):
            continue
        if table[point].lower() in ("autofs", "tmpfs") or not os.path.isdir(point):
            continue
        try:
            found.append(info(point, os.path.basename(point)))
        except OSError:
            continue
    return found


# ---------------------------------------------------------------- Running desktop tools

def subprocess_env() -> Dict[str, str]:
    """The environment for desktop tools JunkZero starts. A packaged JunkZero points
    LD_LIBRARY_PATH at its own libraries; a browser or file manager must not load those."""
    env = dict(os.environ)
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        env["LD_LIBRARY_PATH"] = original
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def restore_library_path() -> None:
    """In a packaged JunkZero, give programs it starts (the browser through webbrowser) the
    system's own LD_LIBRARY_PATH."""
    wanted = subprocess_env()
    for key in ("LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG"):
        if key in wanted:
            os.environ[key] = wanted[key]
        else:
            os.environ.pop(key, None)


FOLDER_PROMPT = "Select a drive or folder to scan for garbage files"
# Exit codes the pickers use when the user cancels (yad also uses 252 for Escape)
_CANCEL_CODES = {1, 252}


def folder_picker_commands(start: str = "", which: Callable[[str], Optional[str]] = shutil.which) -> List[List[str]]:
    """Commands for the folder pickers installed here, best first: zenity (GTK, Xfce and GNOME),
    kdialog (KDE Plasma), yad (installed with MX Linux's own tools)."""
    start_dir = start if start and os.path.isdir(start) else ""
    commands = []
    if which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", f"--title={FOLDER_PROMPT}"]
        if start_dir:
            cmd.append("--filename=" + start_dir.rstrip("/") + "/")
        commands.append(cmd)
    if which("kdialog"):
        commands.append(["kdialog", "--title", FOLDER_PROMPT, "--getexistingdirectory", start_dir or os.path.expanduser("~")])
    if which("yad"):
        cmd = ["yad", "--file", "--directory", f"--title={FOLDER_PROMPT}", "--width=800", "--height=560"]
        if start_dir:
            cmd.append("--filename=" + start_dir.rstrip("/") + "/")
        commands.append(cmd)
    return commands


NO_PICKER_ERROR = ("No folder picker is installed (zenity or kdialog). Type the folder path instead, "
                   "or install one in a terminal: sudo apt install zenity")


def choose_folder(start: str = "", run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                  which: Callable[[str], Optional[str]] = shutil.which) -> Tuple[str, Optional[str]]:
    """(chosen folder or "" when cancelled, error message or None)."""
    commands = folder_picker_commands(start, which)
    if not commands:
        return "", NO_PICKER_ERROR
    last_error = ""
    for command in commands:
        try:
            # No time limit: the picker stays open for as long as the user needs
            res = run(command, capture_output=True, text=True, env=subprocess_env())
        except (OSError, subprocess.SubprocessError) as e:
            last_error = str(e)
            continue
        code = getattr(res, "returncode", 0)
        if code == 0:
            chosen = (res.stdout or "").strip().splitlines()
            chosen = chosen[-1].rstrip("|") if chosen else ""  # yad ends its answer with "|"
            if len(chosen) > 1:
                chosen = chosen.rstrip("/")
            return (chosen, None) if chosen and os.path.isdir(chosen) else ("", None)
        if code in _CANCEL_CODES:
            return "", None
        last_error = (res.stderr or "").strip() or f"{command[0]} exited with {code}"
    return "", f"The folder picker could not open ({last_error}). Type the folder path instead."


def _file_uri(path: str) -> str:
    from urllib.parse import quote
    return "file://" + quote(os.path.abspath(path))


def reveal_commands(path: str, is_file: bool, which: Callable[[str], Optional[str]] = shutil.which) -> List[List[str]]:
    """Commands to try in order to show path in the file manager. For a file, ask the file
    manager to select it (Thunar, Dolphin, Nautilus and Nemo answer org.freedesktop.FileManager1),
    else open the folder it is in."""
    commands = []
    if is_file and which("dbus-send"):
        commands.append([
            "dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
            f"array:string:{_file_uri(path)}", "string:",
        ])
    commands.append(["xdg-open", os.path.dirname(path) if is_file else path])
    return commands


def reveal(path: str, is_file: bool, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
           popen: Callable[..., object] = subprocess.Popen) -> None:
    commands = reveal_commands(path, is_file)
    for command in commands[:-1]:
        try:
            if run(command, capture_output=True, timeout=10, env=subprocess_env()).returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            continue
    popen(commands[-1], env=subprocess_env())


def trash_folder() -> str:
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(data, "Trash", "files")


def open_trash_command(which: Callable[[str], Optional[str]] = shutil.which) -> List[str]:
    """The Trash in the file manager (trash:/// works in Thunar, Nautilus, Nemo and Dolphin)."""
    if which("gio"):
        return ["gio", "open", "trash:///"]
    return ["xdg-open", trash_folder()]


def quote_command(args: List[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)


def desktop_name() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "unknown"
