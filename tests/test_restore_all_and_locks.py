"""Restore brings back every item of a cleanup, and read-only attributes on non-Mac disks don't block deletes."""
import os
import stat
import sys

import pytest

from app.engine import cleaner, osinfo, permissions, recycle, storage


def _drive(tmp_path, monkeypatch, n):
    """A drive without a Recycle Bin holding n junk files in nested folders."""
    drive = tmp_path / "files"
    paths = []
    for i in range(n):
        f = drive / "Red" / f"d{i % 7}" / f"f{i}.tmp"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(str(i))
        paths.append(str(f))
    monkeypatch.setattr(recycle, "has_recycle_bin", lambda p: False)
    monkeypatch.setattr(recycle, "mount_point", lambda p: str(drive))
    return drive, paths


def _cleanup(paths):
    result = cleaner.delete_items([{"path": p, "size_bytes": 1} for p in paths], exclusions=[])
    record = storage.record_cleanup(result.model_dump(), paths, "scan results")
    return result, record


def test_history_keeps_every_item_and_restore_puts_all_back(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "HISTORY_PATHS_LIMIT", 5)
    drive, paths = _drive(tmp_path, monkeypatch, 23)
    result, record = _cleanup(paths)
    assert result.deleted_count == 23 and not any(os.path.exists(p) for p in paths)
    assert len(storage.load_history()[0]["paths"]) == 5           # what the history list shows
    assert len(storage.find_cleanup(record["timestamp"])["held"]) == 23
    outcome = recycle.restore_cleanup(storage.find_cleanup(record["timestamp"]))
    assert outcome["errors"] == []
    assert all(open(p).read() for p in paths)


def test_an_older_record_with_only_some_items_still_restores_the_whole_batch(tmp_path, monkeypatch):
    drive, paths = _drive(tmp_path, monkeypatch, 30)
    result = cleaner.delete_items([{"path": p, "size_bytes": 1} for p in paths], exclusions=[])
    # What 1.1.0 saved: only the first few items (here 4) and where they went
    first = paths[:4]
    old_record = {"timestamp": 1.0, "mode": "recycle_bin", "paths": first, "paths_truncated": 26,
                  "held": {p: result.held[p] for p in first}, "trash_ids": {}, "restored": []}
    outcome = recycle.restore_cleanup(old_record)
    assert outcome["errors"] == []
    assert all(os.path.exists(p) for p in paths)
    batch, root = recycle._batch_of(next(iter(result.held.values())))
    assert root == str(drive)
    assert not os.path.exists(batch) or os.listdir(batch) == []  # nothing left behind in the batch


def test_batch_restore_never_overwrites_and_keeps_going(tmp_path, monkeypatch):
    drive, paths = _drive(tmp_path, monkeypatch, 6)
    result = cleaner.delete_items([{"path": p, "size_bytes": 1} for p in paths], exclusions=[])
    record = {"timestamp": 2.0, "mode": "recycle_bin", "paths": paths[:1], "held": {paths[0]: result.held[paths[0]]},
              "trash_ids": {}, "restored": []}
    with open(paths[3], "w") as f:   # something new appeared where an item was
        f.write("new")
    outcome = recycle.restore_cleanup(record)
    assert open(paths[3]).read() == "new"
    assert any(e["path"] == paths[3] for e in outcome["errors"])
    assert all(os.path.exists(p) for p in paths)
    assert os.path.exists(result.held[paths[3]])  # the held copy is kept, not lost


def test_failed_move_leaves_no_empty_folders_in_the_holding_folder(tmp_path, monkeypatch):
    drive, paths = _drive(tmp_path, monkeypatch, 1)
    batch = recycle.new_holding_batch(str(drive))
    monkeypatch.setattr(recycle.os, "rename", lambda a, b: (_ for _ in ()).throw(PermissionError(1, "Operation not permitted")))
    with pytest.raises(PermissionError):
        recycle.move_to_holding(paths[0], batch)
    assert os.listdir(batch) == []


def test_clearing_history_removes_the_full_lists(tmp_path, monkeypatch):
    _drive_path, paths = _drive(tmp_path, monkeypatch, 3)
    _cleanup(paths)
    folder = storage.data_dir() / storage.CLEANUP_DETAILS_DIR
    assert len(os.listdir(folder)) == 1
    storage.clear_history()
    # Its items are still in the holding folder: the list stays their record until they are purged
    assert len(os.listdir(folder)) == 1
    # A cleanup with nothing set aside loses its list with the history
    for name in os.listdir(folder):
        os.remove(folder / name)
    storage.record_cleanup({"deleted_count": 1}, [paths[0]], "test")
    assert len(os.listdir(folder)) == 1
    storage.clear_history()
    assert os.listdir(folder) == []


# ---------------------------------------------------------------- Read-only attribute vs Finder Locked

@pytest.fixture
def mac_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)

    def use(fs):
        monkeypatch.setattr(recycle, "_mac_mounts", lambda: {str(tmp_path): fs})
        monkeypatch.setattr(recycle, "mount_point", lambda p: str(tmp_path))
    return use


def _flagged(monkeypatch, flagged, flags=0x2):
    real = os.lstat

    class St:
        def __init__(self, st, extra):
            self._st, self.st_flags = st, extra

        def __getattr__(self, name):
            return getattr(self._st, name)
    monkeypatch.setattr(permissions.os, "lstat",
                        lambda p, *a, **k: St(real(p), flags if str(p) in flagged else 0))


@pytest.mark.parametrize("fs, attribute", [("smbfs", True), ("exfat", True), ("msdos", True), ("ntfs", True),
                                           ("apfs", False), ("hfs", False)])
def test_lock_flag_is_the_read_only_attribute_only_on_non_mac_disks(mac_disk, monkeypatch, tmp_path, fs, attribute):
    mac_disk(fs)
    f = tmp_path / "setup.exe"
    f.write_text("x")
    _flagged(monkeypatch, {str(f)})
    assert permissions.is_read_only_attribute(str(f)) is attribute
    assert permissions.is_finder_locked(str(f)) is (not attribute)


def test_read_only_attribute_is_cleared_and_the_move_retried(mac_disk, monkeypatch, tmp_path):
    mac_disk("smbfs")
    f = tmp_path / "setup.exe"
    f.write_text("x")
    _flagged(monkeypatch, {str(f)})
    cleared = []
    monkeypatch.setattr(permissions, "unlock", lambda p: cleared.append(p) or True)
    tries = []

    def move():
        tries.append(1)
        if len(tries) == 1:
            raise PermissionError(1, "Operation not permitted")
        return "moved"
    assert permissions.with_retry(move, str(f)) == "moved"
    assert cleared == [str(f)]


def test_finder_locked_items_are_unlocked_only_when_asked(mac_disk, monkeypatch, tmp_path):
    mac_disk("apfs")
    f = tmp_path / "keep.doc"
    f.write_text("x")
    _flagged(monkeypatch, {str(f)})
    cleared = []
    monkeypatch.setattr(permissions, "unlock", lambda p: cleared.append(p) or True)
    assert permissions.make_writable(str(f)) is False and cleared == []
    assert permissions.make_writable(str(f), unlock_locked=True) is True and cleared == [str(f)]


def test_a_locked_folder_holding_the_item_is_never_unlocked_for_it(mac_disk, monkeypatch, tmp_path):
    mac_disk("apfs")
    folder = tmp_path / "Locked folder"
    folder.mkdir()
    f = folder / "a.tmp"
    f.write_text("x")
    _flagged(monkeypatch, {str(folder)})
    cleared = []
    monkeypatch.setattr(permissions, "unlock", lambda p: cleared.append(p) or True)
    permissions.make_writable(str(f), unlock_locked=True)
    assert str(folder) not in cleared


def test_system_immutable_is_never_cleared(monkeypatch, tmp_path):
    f = tmp_path / "a"
    f.write_text("x")
    _flagged(monkeypatch, {str(f)}, flags=0x2 | 0x20000)
    assert permissions.unlock(str(f)) is False


def test_explain_tells_locked_from_read_only_attribute(mac_disk, monkeypatch, tmp_path):
    mac_disk("apfs")
    f = tmp_path / "a.doc"
    f.write_text("x")
    _flagged(monkeypatch, {str(f)})
    kind, why = permissions.explain(PermissionError(1, "Operation not permitted"), str(f))
    assert kind == permissions.KIND_LOCKED and "Unlock and delete" in why
