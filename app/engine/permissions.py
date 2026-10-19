"""Permission problems when scanning or deleting: fix what JunkZero safely can, explain the rest.

JunkZero never uses administrator rights and never unlocks what the user locked. It only makes
the user's own files and folders writable so a delete they confirmed can finish (a read-only
file on Windows, a read-only folder on a Mac), and otherwise says why and how to fix it.
"""
from __future__ import annotations
import errno
import os
import stat
import sys
from typing import Any, Callable, Optional, Tuple

from app.engine import osinfo

KIND_PRIVACY = "mac_privacy"        # macOS privacy settings kept JunkZero out
KIND_LOCKED = "locked"              # locked in Finder (or immutable)
KIND_NOT_YOURS = "not_yours"        # belongs to another account or the system
KIND_FOLDER = "folder_read_only"    # the folder holding it can't be changed
KIND_READ_ONLY_DISK = "read_only_disk"
KIND_DENIED = "access_denied"       # Windows "Access is denied" with no better reason

# System Settings > Privacy & Security > Full Disk Access
MAC_FULL_DISK_ACCESS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

_WIN_ACCESS_DENIED = 5
_WIN_SHARING_VIOLATION = 32
_PERMISSION_WORDS = ("permission", "not permitted", "access is denied", "insufficient access privileges",
                     "not allowed", "operation not permitted")


def privacy_app() -> str:
    """The app macOS asks about: the packaged app, or the Terminal that run.command opens."""
    if getattr(sys, "frozen", False):
        return "JunkZero"
    return "Terminal (the app run.command opens JunkZero in)"


def mac_privacy_fix() -> str:
    return (f"Give {privacy_app()} Full Disk Access in System Settings > Privacy & Security > "
            "Full Disk Access, then restart JunkZero")


def _os_error(err: BaseException) -> Optional[OSError]:
    seen = 0
    while err is not None and seen < 5:
        if isinstance(err, OSError) and (err.errno or getattr(err, "winerror", None)):
            return err
        err = err.__cause__ or err.__context__
        seen += 1
    return None


def is_permission_error(err: BaseException) -> bool:
    """True for errors that mean "not allowed" (as opposed to missing, in use or full)."""
    os_err = _os_error(err)
    if os_err is not None:
        if getattr(os_err, "winerror", None) == _WIN_SHARING_VIOLATION:
            return False
        if os_err.errno in (errno.EPERM, errno.EACCES, errno.EROFS) or \
                getattr(os_err, "winerror", None) == _WIN_ACCESS_DENIED:
            return True
    text = str(err).lower()
    return any(word in text for word in _PERMISSION_WORDS)


_UF_IMMUTABLE = getattr(stat, "UF_IMMUTABLE", 0x2)
_SF_IMMUTABLE = getattr(stat, "SF_IMMUTABLE", 0x20000)
# Disks whose "locked" flag is really the Windows read-only attribute: macOS shows that attribute
# on FAT, exFAT, NTFS and network (SMB) disks as Finder's Locked flag
_READ_ONLY_ATTRIBUTE_FS = {"msdos", "exfat", "ntfs", "ufsd_ntfs", "tuxera_ntfs", "smbfs", "cifs"}


def _flags(path: str) -> int:
    try:
        return getattr(os.lstat(path), "st_flags", 0) or 0
    except OSError:
        return 0


def is_locked(path: str) -> bool:
    """Locked in Finder (user immutable flag) or immutable for everyone (system flag)."""
    return bool(_flags(path) & (_UF_IMMUTABLE | _SF_IMMUTABLE))


def _disk_fs(path: str) -> str:
    from app.engine import recycle  # recycle imports nothing from here
    try:
        return (recycle._mac_mounts().get(recycle.mount_point(path)) or "").lower()
    except Exception:
        return ""


def is_read_only_attribute(path: str) -> bool:
    """The "lock" is just the Windows read-only attribute (a FAT, exFAT, NTFS or network disk on a
    Mac), not something the user locked in Finder on a Mac disk."""
    flags = _flags(path)
    return bool(flags & _UF_IMMUTABLE) and not flags & _SF_IMMUTABLE and osinfo.is_macos() \
        and _disk_fs(path) in _READ_ONLY_ATTRIBUTE_FS


def is_finder_locked(path: str) -> bool:
    """Locked on purpose (Finder's Locked on a Mac disk, or immutable for everyone)."""
    return is_locked(path) and not is_read_only_attribute(path)


def unlock(path: str) -> bool:
    """Clear the user's Locked flag on their own item (never the system flag). True if cleared."""
    flags = _flags(path)
    if not flags & _UF_IMMUTABLE or flags & _SF_IMMUTABLE or _not_mine(path) or not hasattr(os, "chflags"):
        return False
    try:
        os.chflags(path, flags & ~_UF_IMMUTABLE, follow_symlinks=False)
        return True
    except (OSError, NotImplementedError, TypeError):
        try:
            os.chflags(path, flags & ~_UF_IMMUTABLE)
            return True
        except (OSError, NotImplementedError):
            return False


def _owner_name(uid: int) -> str:
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return str(uid)


def _not_mine(path: str) -> Optional[str]:
    """The owner's name if path belongs to another account (POSIX), else None."""
    if not hasattr(os, "getuid"):
        return None
    try:
        uid = os.lstat(path).st_uid
    except OSError:
        return None
    return None if uid == os.getuid() else _owner_name(uid)


def _whose(uid: int) -> str:
    name = _owner_name(uid)
    return "the system" if name == "root" else f"the account {name!r}"


def removal_blocker(path: str) -> str:
    """Why the user can't remove path, in plain words, or "" if they can (or it can't be told).
    Only what JunkZero can't fix counts: a folder or item belonging to another account or the
    system. The user's own read-only folder doesn't, because make_writable() fixes it, and a
    read-only disk is explained per disk instead."""
    if osinfo.is_windows() or not hasattr(os, "getuid") or os.getuid() == 0:
        return ""
    uid = os.getuid()
    parent = os.path.dirname(os.path.abspath(path))
    try:
        st, parent_st = os.lstat(path), os.stat(parent)
        if os.statvfs(parent).f_flag & os.ST_RDONLY:
            return ""
    except (OSError, AttributeError):
        return ""
    rights = "and JunkZero doesn't use administrator rights"
    if parent_st.st_uid != uid and not os.access(parent, os.W_OK | os.X_OK):
        return f"the folder it is in belongs to {_whose(parent_st.st_uid)}, {rights}"
    # In a shared folder (sticky bit, like /tmp) only the owner may remove an item
    if parent_st.st_mode & stat.S_ISVTX and st.st_uid != uid and parent_st.st_uid != uid:
        return f"it belongs to {_whose(st.st_uid)}, {rights}"
    # Moving a folder into the Trash changes it too, and the Trash refuses a file of another
    # account's that the user can't write (root's files left by sudo in the user's own folder)
    if st.st_uid != uid and not stat.S_ISLNK(st.st_mode) and \
            not os.access(path, (os.W_OK | os.X_OK) if stat.S_ISDIR(st.st_mode) else os.W_OK):
        return f"it belongs to {_whose(st.st_uid)}, {rights}"
    return ""


def foreign_folder(path: str, st: os.stat_result) -> bool:
    """True for a folder (inside something being listed) that belongs to another account, can't be
    changed by the user and isn't empty: what is in it can't be removed, so neither can the item
    holding it (deleting would stop halfway, and its space could never be freed from the Trash)."""
    if not hasattr(os, "getuid") or osinfo.is_windows():
        return False
    uid = os.getuid()
    if uid == 0 or st.st_uid == uid or not stat.S_ISDIR(st.st_mode) or os.access(path, os.W_OK | os.X_OK):
        return False
    try:
        with os.scandir(path) as it:
            return next(it, None) is not None
    except OSError:
        return True  # Can't even look inside


def inner_blocker(path: str, foreign: Any) -> str:
    """Why a folder can't be removed whole because of something inside it, or "". foreign holds
    the folders foreign_folder() found while the scan went through it."""
    if not foreign:
        return ""
    prefix = os.path.normcase(os.path.abspath(path)).rstrip(os.sep) + os.sep
    for inner in list(foreign):
        if os.path.normcase(inner).startswith(prefix):
            try:
                owner = _whose(os.lstat(inner).st_uid)
            except OSError:
                owner = "another account"
            return f"{inner} inside it belongs to {owner}, and JunkZero doesn't use administrator rights"
    return ""


def explain(err: BaseException, path: str) -> Optional[Tuple[str, str]]:
    """(kind, what to tell the user) for a permission problem with path, or None if err is not one."""
    if not is_permission_error(err):
        return None
    os_err = _os_error(err)
    code = os_err.errno if os_err is not None else None
    # The error came from something inside the folder being removed: name it
    inner = getattr(os_err, "filename", None) if os_err is not None else None
    if isinstance(inner, str) and code != errno.EROFS and os.path.isdir(path) and \
            os.path.normcase(os.path.abspath(inner)).startswith(os.path.normcase(os.path.abspath(path)) + os.sep):
        found = explain(OSError(code, os_err.strerror or "") if code else err, inner)
        if found:
            return found[0], f"something inside it can't be removed ({inner}): {found[1]}"
    parent = os.path.dirname(os.path.abspath(path))

    if code == errno.EROFS:
        return KIND_READ_ONLY_DISK, "the disk is read-only, so nothing on it can be deleted"
    if is_finder_locked(path) or is_finder_locked(parent):
        where = "it" if is_finder_locked(path) else "the folder it is in"
        how = "use Unlock and delete, or select it in Finder, choose File > Get Info and untick Locked" \
            if osinfo.is_macos() else "remove the immutable flag"
        return KIND_LOCKED, f"{where} is locked: {how}"
    owner = _not_mine(path)
    if owner:
        return KIND_NOT_YOURS, (f"it belongs to {'the system' if owner == 'root' else 'the account ' + repr(owner)}, "
                                "and JunkZero doesn't use administrator rights")
    parent_owner = _not_mine(parent)
    if parent_owner or (hasattr(os, "access") and os.path.isdir(parent) and not os.access(parent, os.W_OK)):
        return KIND_FOLDER, f"you can't change the folder it is in ({parent})"
    if osinfo.is_macos():
        # Your own file, not locked, folder writable: macOS privacy settings blocked JunkZero
        return KIND_PRIVACY, f"macOS didn't allow JunkZero to change it. {mac_privacy_fix()}"
    if osinfo.is_windows():
        return KIND_DENIED, ("Windows denied access: it may belong to another account, be in use by a "
                             "system service, or need administrator rights, which JunkZero doesn't use")
    return KIND_DENIED, "you don't have permission to change it"


def make_writable(path: str, unlock_locked: bool = False) -> bool:
    """Make the user's own file or folder (and the folder holding it) writable so it can be removed.
    The Windows read-only attribute is cleared (on Windows, and on FAT, exFAT, NTFS and network
    disks on a Mac, where it shows as Locked). Something locked on purpose is unlocked only with
    unlock_locked (the user chose Unlock and delete), and only the item itself, never its folder.
    Never touches what belongs to someone else. True if anything changed."""
    changed = False
    for target in (path, os.path.dirname(os.path.abspath(path))):
        if not os.path.lexists(target) or os.path.islink(target) or _not_mine(target):
            continue
        if is_locked(target):
            allowed = is_read_only_attribute(target) or (unlock_locked and target == path)
            if not (allowed and unlock(target)):
                continue
            changed = True
        try:
            mode = os.lstat(target).st_mode
            if osinfo.is_windows():
                if not mode & stat.S_IWRITE:
                    os.chmod(target, stat.S_IWRITE | stat.S_IREAD)  # clears the read-only attribute
                    changed = True
            elif not mode & stat.S_IWUSR or (stat.S_ISDIR(mode) and (mode & stat.S_IRWXU) != stat.S_IRWXU):
                # A folder also needs reading and entering to empty it
                os.chmod(target, mode | stat.S_IWUSR | (stat.S_IRUSR | stat.S_IXUSR if stat.S_ISDIR(mode) else 0))
                changed = True
        except OSError:
            continue
    return changed


def retry_writable(func: Callable, path: str, err: BaseException, unlock_locked: bool = False) -> None:
    """shutil.rmtree error handler: on a permission error, make the user's own item writable and try once more."""
    if is_permission_error(err) and make_writable(path, unlock_locked):
        func(path)
        return
    raise err


def with_retry(action: Callable[[], Any], path: str, unlock_locked: bool = False) -> Any:
    """Run action (a move or delete of path); on a permission error, make path writable and try once more."""
    try:
        return action()
    except Exception as err:
        if is_permission_error(err) and make_writable(path, unlock_locked):
            return action()
        raise
