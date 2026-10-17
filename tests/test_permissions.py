"""Permission problems: JunkZero fixes the user's own read-only items, and explains everything else."""
import errno
import os
import stat
import sys

import pytest

from app import main
from app.config import ScanOptions
from app.engine import cleaner, osinfo, permissions, recycle
from app.engine.scanner import FastScanner
from tests.helpers import api_client

posix_only = pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                                reason="POSIX file modes (and not running as root, which ignores them)")


# ---------------------------------------------------------------- Fixed automatically

def test_read_only_file_is_deleted_permanently(tmp_path):
    f = tmp_path / "old.tmp"
    f.write_text("x")
    os.chmod(f, stat.S_IREAD)  # Windows: the read-only attribute
    result = cleaner.delete_items([{"path": str(f)}], permanent=True, exclusions=[])
    assert result.deleted_count == 1 and not f.exists()


def test_folder_with_read_only_files_is_deleted_permanently(tmp_path):
    folder = tmp_path / "build"
    (folder / "sub").mkdir(parents=True)
    for name in ("a.o", "sub/b.o"):
        p = folder / name
        p.write_text("x")
        os.chmod(p, stat.S_IREAD)
    result = cleaner.delete_items([{"path": str(folder), "is_directory": True}], permanent=True, exclusions=[])
    assert result.deleted_count == 1 and not folder.exists()


@posix_only
def test_folder_with_a_read_only_subfolder_is_deleted_permanently(tmp_path):
    folder = tmp_path / "cache"
    sub = folder / "locked-down"
    sub.mkdir(parents=True)
    (sub / "c.tmp").write_text("x")
    os.chmod(sub, 0o555)  # can't remove what's inside until the folder is writable again
    try:
        result = cleaner.delete_items([{"path": str(folder), "is_directory": True}], permanent=True, exclusions=[])
    finally:
        if sub.exists():
            os.chmod(sub, 0o755)
    assert result.deleted_count == 1 and not folder.exists()


def test_make_writable_leaves_locked_and_other_peoples_items_alone(tmp_path, monkeypatch):
    f = tmp_path / "x.tmp"
    f.write_text("x")
    os.chmod(f, stat.S_IREAD)
    monkeypatch.setattr(permissions, "is_locked", lambda p: True)
    assert permissions.make_writable(str(f)) is False
    monkeypatch.setattr(permissions, "is_locked", lambda p: False)
    monkeypatch.setattr(permissions, "_not_mine", lambda p: "root")
    assert permissions.make_writable(str(f)) is False
    os.chmod(f, stat.S_IWRITE | stat.S_IREAD)


# ---------------------------------------------------------------- Explained

def _perm(code=errno.EPERM, text="Operation not permitted"):
    return PermissionError(code, text)


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)


def test_own_unlocked_file_blocked_on_a_mac_is_privacy(mac, tmp_path, monkeypatch):
    monkeypatch.setattr(permissions, "_not_mine", lambda p: None)
    f = tmp_path / "a.tmp"
    f.write_text("x")
    kind, why = permissions.explain(_perm(), str(f))
    assert kind == permissions.KIND_PRIVACY
    assert "Full Disk Access" in why and "Privacy & Security" in why


def test_packaged_app_names_itself_for_full_disk_access(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert permissions.privacy_app() == "JunkZero"
    monkeypatch.delattr(sys, "frozen")
    assert permissions.privacy_app().startswith("Terminal")


def test_locked_file_says_how_to_unlock(mac, tmp_path, monkeypatch):
    f = tmp_path / "a.tmp"
    f.write_text("x")
    monkeypatch.setattr(permissions, "is_locked", lambda p: p == str(f))
    kind, why = permissions.explain(_perm(), str(f))
    assert kind == permissions.KIND_LOCKED and "Get Info" in why and "Locked" in why


def test_someone_elses_file_is_named_and_not_forced(tmp_path, monkeypatch):
    f = tmp_path / "a.tmp"
    f.write_text("x")
    monkeypatch.setattr(permissions, "_not_mine", lambda p: "root" if p == str(f) else None)
    kind, why = permissions.explain(_perm(errno.EACCES, "Permission denied"), str(f))
    assert kind == permissions.KIND_NOT_YOURS and "the system" in why and "administrator" in why


def test_read_only_disk_is_named(tmp_path):
    kind, _ = permissions.explain(OSError(errno.EROFS, "Read-only file system"), str(tmp_path / "a"))
    assert kind == permissions.KIND_READ_ONLY_DISK


def test_trash_refusal_without_an_error_number_is_recognised(mac, tmp_path, monkeypatch):
    monkeypatch.setattr(permissions, "_not_mine", lambda p: None)
    err = OSError("You don’t have permission to access “a.tmp”.")
    assert permissions.is_permission_error(err)
    assert permissions.explain(err, str(tmp_path / "a.tmp"))[0] == permissions.KIND_PRIVACY


def test_windows_access_denied(monkeypatch, tmp_path):
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    monkeypatch.setattr(permissions, "_not_mine", lambda p: None)
    err = PermissionError(errno.EACCES, "Access is denied")
    err.winerror = 5
    kind, why = permissions.explain(err, str(tmp_path / "a"))
    assert kind == permissions.KIND_DENIED and "administrator" in why


def test_in_use_and_missing_are_not_permission_problems(tmp_path):
    busy = PermissionError(errno.EACCES, "The process cannot access the file")
    busy.winerror = 32
    assert permissions.explain(busy, str(tmp_path)) is None
    assert permissions.explain(FileNotFoundError(errno.ENOENT, "No such file"), str(tmp_path)) is None


def test_delete_reports_the_reason_and_kind(mac, tmp_path, monkeypatch):
    f = tmp_path / "a.tmp"
    f.write_text("x")
    monkeypatch.setattr(permissions, "_not_mine", lambda p: None)
    monkeypatch.setattr(recycle, "recycle_bin_takes", lambda p, s: True)
    monkeypatch.setitem(sys.modules, "Foundation", object())

    def refuse(path):
        raise OSError(errno.EPERM, "Operation not permitted", path)
    monkeypatch.setattr(cleaner.send2trash, "send2trash", refuse)
    result = cleaner.delete_items([{"path": str(f), "size_bytes": 1}], exclusions=[])
    err = result.errors[0]
    assert err["kind"] == permissions.KIND_PRIVACY
    assert err["error"].startswith("Can't delete: macOS didn't allow JunkZero") and f.exists()


# ---------------------------------------------------------------- Scans report what they couldn't read

def test_scan_counts_folders_it_could_not_read(tmp_path, monkeypatch):
    blocked = tmp_path / "Private"
    blocked.mkdir()
    (tmp_path / "ok.crdownload").write_text("x")
    real = os.scandir

    def scandir(path="."):
        if str(path) == str(blocked):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real(path)
    monkeypatch.setattr("app.engine.scanner.os.scandir", scandir)
    scanner = FastScanner(ScanOptions(target_path=str(tmp_path), cloud_folders=[]))
    scanner.run_scan()
    assert scanner.stats.unreadable_dirs == 1 and scanner.stats.privacy_blocked_dirs == 0
    assert scanner.stats.unreadable_examples == [str(blocked)]


def test_scan_on_a_mac_tells_privacy_blocks_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    blocked = tmp_path / "Desktop"
    blocked.mkdir()
    real = os.scandir

    def scandir(path="."):
        if str(path) == str(blocked):
            raise PermissionError(errno.EPERM, "Operation not permitted", str(path))
        return real(path)
    monkeypatch.setattr("app.engine.scanner.os.scandir", scandir)
    scanner = FastScanner(ScanOptions(target_path=str(tmp_path), cloud_folders=[], include_leftovers=False))
    scanner.run_scan()
    assert scanner.stats.privacy_blocked_dirs == 1


# ---------------------------------------------------------------- Opening the settings

def test_privacy_settings_open_on_a_mac(mac, monkeypatch):
    opened = []
    monkeypatch.setattr(main.subprocess, "Popen", lambda args: opened.append(args))
    res = api_client().post("/api/system/open-privacy-settings").json()
    assert opened == [["open", permissions.MAC_FULL_DISK_ACCESS_URL]] and res["success"]


def test_privacy_settings_are_mac_only(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    assert api_client().post("/api/system/open-privacy-settings").status_code == 501
