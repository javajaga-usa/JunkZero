"""Tell whether a file is open in another program, so JunkZero never deletes it from under it.

* Office and LibreOffice leave a lock file next to an open document ("~$port.docx", ".~lock.report.docx#").
* Windows: a file another program holds open can't be opened for exclusive use.
* macOS: the files open in the user's programs come from `lsof`; Linux: from /proc.
"""
from __future__ import annotations
import os
import subprocess
from typing import Optional, Set

from app.engine import osinfo

_ERROR_SHARING_VIOLATION = 32
_ERROR_LOCK_VIOLATION = 33


def office_lock_file(path: str) -> Optional[str]:
    """The Office / LibreOffice lock file showing path is open in an editor, if there is one."""
    folder, name = os.path.split(path)
    if not name or name.startswith(("~$", ".~lock.")):
        return None
    candidates = {"~$" + name, "~$" + name[1:], "~$" + name[2:], ".~lock." + name + "#"}
    for candidate in candidates:
        if len(candidate) > 2 and os.path.isfile(os.path.join(folder, candidate)):
            return os.path.join(folder, candidate)
    return None


def _windows_file_in_use(path: str) -> bool:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    # GENERIC_READ, no sharing, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL
    handle = kernel32.CreateFileW(path, 0x80000000, 0, None, 3, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return ctypes.get_last_error() in (_ERROR_SHARING_VIOLATION, _ERROR_LOCK_VIOLATION)
    kernel32.CloseHandle(handle)
    return False


def _lsof_open_files() -> Set[str]:
    try:
        out = subprocess.run(["lsof", "-w", "-Fn", "-u", str(os.getuid())],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line[1:] for line in out.splitlines() if line.startswith("n/")}


def _proc_open_files() -> Set[str]:
    found: Set[str] = set()
    me = os.getpid()
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit() and int(p) != me]
    except OSError:
        return found
    for pid in pids:
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue  # Another user's process
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.startswith("/"):
                found.add(target)
    return found


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class OpenFileCheck:
    """Built once per cleanup (listing open files is slow); reason() per item."""

    def __init__(self, open_files: Optional[Set[str]] = None):
        if open_files is None and not osinfo.is_windows():
            open_files = _lsof_open_files() if osinfo.is_macos() else _proc_open_files()
        self._open = {_norm(p) for p in (open_files or ())}

    def reason(self, path: str, is_dir: bool) -> Optional[str]:
        """Why the item is in use, or None if it can be deleted."""
        if not is_dir and office_lock_file(path):
            return "open in Office or LibreOffice"
        norm = _norm(path)
        if self._open:
            if norm in self._open:
                return "open in another program"
            if is_dir and any(p.startswith(norm + os.sep) for p in self._open):
                return "a file inside it is open in another program"
        if osinfo.is_windows() and not is_dir:
            try:
                if _windows_file_in_use(path):
                    return "open in another program"
            except (OSError, AttributeError):
                pass
        return None
