"""Fixes from the full audit before 1.1.0: the app token, deletes, restoring, settings files
and scheduled runs."""
import errno
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from app import main
from app.config import CAT_DUPLICATES
from app.engine import cleaner, duplicates, inuse, osinfo, recycle, scheduler, storage
from app.engine.inuse import OpenFileCheck
from tests.helpers import api_client, offer

client = api_client()


# ------------------------------------------------------------- App token

def test_live_scan_stream_takes_the_token_in_its_address():
    bare = api_client(headers={main.TOKEN_HEADER: ""})
    main.current_scanner = None
    assert bare.get("/api/scan/stream").status_code == 403
    assert bare.get("/api/scan/stream", params={"token": "wrong"}).status_code == 403
    assert bare.get("/api/scan/stream", params={"token": main.API_TOKEN}).status_code == 200
    # Only the stream: other reads still need the header
    assert bare.get("/api/history", params={"token": main.API_TOKEN}).status_code == 403


def test_odd_token_header_is_refused_not_a_crash():
    bare = api_client(headers={main.TOKEN_HEADER: ""})
    res = bare.post("/api/scan/stop", headers={main.TOKEN_HEADER: b"\xff\xfe"})
    assert res.status_code == 403


def test_absurd_scan_numbers_are_rejected():
    for body in ({"min_size_mb": 1e308}, {"stale_days": 10 ** 30}, {"old_download_days": 10 ** 30}):
        assert client.post("/api/scan/start", json={"target_path": ".", **body}).status_code == 422


def test_show_in_finder_never_opens_an_app(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    assert main.reveal_command("/Users/a/Downloads/Tool.app", False) == ["open", "-R", "/Users/a/Downloads/Tool.app"]
    assert main.reveal_command("/Users/a/Pictures/Photos Library.photoslibrary/", False)[1] == "-R"
    assert main.reveal_command("/Users/a/Downloads", False) == ["open", "/Users/a/Downloads"]


def test_scheduled_report_items_stop_being_deletable_after_a_scan(tmp_path):
    reported = tmp_path / "old.tmp"
    reported.write_text("x")
    storage.save_report({"items": [{"path": str(reported), "size_bytes": 1, "is_directory": False}]})
    main.current_scanner = None
    assert main._key(str(reported)) in main.scan_results()
    offer([])  # A scan in the app that no longer lists it (excluded since, for example)
    res = client.post("/api/clean", json={"items": [{"path": str(reported)}], "permanent": True})
    assert res.json()["deleted_count"] == 0 and reported.exists()


# ------------------------------------------------------------- Deletes

def test_two_deletes_at_once_never_remove_every_copy(tmp_path, monkeypatch):
    a, b = tmp_path / "a.dat", tmp_path / "b.dat"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    real_same = duplicates.same_content

    def slow_same(x, y):
        time.sleep(0.2)
        return real_same(x, y)

    monkeypatch.setattr(cleaner, "same_content", slow_same)
    results = []
    jobs = [
        threading.Thread(target=lambda p=p, o=o: results.append(cleaner.delete_items(
            [{"path": str(p), "size_bytes": 4, "category": CAT_DUPLICATES, "duplicate_paths": [str(o)]}],
            permanent=True)))
        for p, o in ((a, b), (b, a))
    ]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join()
    assert sum(r.deleted_count for r in results) == 1
    assert a.exists() or b.exists()


def test_a_folder_holding_a_repository_is_never_deleted(tmp_path):
    build = tmp_path / "proj" / "build"
    (build / ".git").mkdir(parents=True)
    (build / "out.o").write_text("x")
    result = cleaner.delete_items([{"path": str(build), "is_directory": True, "size_bytes": 1}], permanent=True)
    assert result.deleted_count == 0 and "holds a .git folder" in result.errors[0]["error"]
    assert (build / ".git").is_dir()


def test_items_too_big_for_the_recycle_bin_are_set_aside(tmp_path, monkeypatch):
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    monkeypatch.setattr(recycle, "has_recycle_bin", lambda p: True)
    monkeypatch.setattr(recycle, "_windows_recycle_settings", lambda root: (100, False))
    assert recycle.recycle_bin_takes(str(tmp_path), 99)
    assert not recycle.recycle_bin_takes(str(tmp_path), 100)
    # "Don't move files to the Recycle Bin" is switched on for the drive
    monkeypatch.setattr(recycle, "_windows_recycle_settings", lambda root: (None, True))
    assert not recycle.recycle_bin_takes(str(tmp_path), 1)


def test_item_the_recycle_bin_would_erase_goes_to_the_holding_folder(tmp_path, monkeypatch):
    big = tmp_path / "big.iso"
    big.write_bytes(b"x" * 10)
    sent = []
    monkeypatch.setattr(recycle, "recycle_bin_takes", lambda p, size: False)
    monkeypatch.setattr(recycle, "mount_point", lambda p: str(tmp_path))
    monkeypatch.setattr(cleaner.send2trash, "send2trash", sent.append)
    result = cleaner.delete_items([{"path": str(big), "size_bytes": 10}])
    assert result.deleted_count == 1 and sent == [] and not big.exists()
    assert os.path.isfile(result.held[str(big)])


def test_unknown_open_files_means_nothing_is_deleted(tmp_path, monkeypatch):
    victim = tmp_path / "a.tmp"
    victim.write_text("x")
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    monkeypatch.setattr(inuse.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=""))
    check = OpenFileCheck()
    assert not check.checked
    result = cleaner.delete_items([{"path": str(victim), "size_bytes": 1}], permanent=True, open_check=check)
    assert result.deleted_count == 0 and victim.exists()
    assert "couldn't check which files are open" in result.errors[0]["error"]


# ------------------------------------------------------------- Restoring

def test_restore_never_moves_over_something_that_appeared(tmp_path, monkeypatch):
    stored, original = tmp_path / "held" / "a.txt", tmp_path / "a.txt"
    stored.parent.mkdir()
    stored.write_text("old")

    def cross_drive(src, dst):
        original.write_text("new")  # Appears just before the copy across drives
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(recycle.os, "rename", cross_drive)
    out = recycle.restore_cleanup({"mode": "recycle_bin", "timestamp": time.time(), "paths": [str(original)],
                                   "held": {str(original): str(stored)}})
    assert out["restored"] == [] and "already" in out["errors"][0]["error"]
    assert original.read_text() == "new" and stored.read_text() == "old"


def test_cleanups_in_the_same_instant_get_their_own_ids(monkeypatch):
    monkeypatch.setattr(storage.time, "time", lambda: 1000.0)
    first = storage.record_cleanup({"deleted_count": 1}, ["a"], "test")
    second = storage.record_cleanup({"deleted_count": 1}, ["b"], "test")
    assert first["timestamp"] != second["timestamp"]
    assert storage.find_cleanup(second["timestamp"])["paths"] == ["b"]


# ------------------------------------------------------------- Settings and scheduled runs

def test_a_damaged_settings_file_is_kept_aside(tmp_path):
    path = storage.data_dir() / "settings.json"
    path.write_text('{"exclusions": ["C:/Keep"')
    assert storage.load_settings() == {}
    storage.update_settings(theme="dark")
    kept = [p for p in os.listdir(storage.data_dir()) if p.startswith("settings.json.damaged-")]
    assert len(kept) == 1
    assert (storage.data_dir() / kept[0]).read_text() == '{"exclusions": ["C:/Keep"'


def test_settings_writes_leave_no_temp_files_and_survive_odd_values():
    storage.update_settings(exclusions=5, custom_rules="x")
    assert storage.get_exclusions() == [] and storage.get_custom_rules() == []
    assert [p for p in os.listdir(storage.data_dir()) if p.endswith(".tmp")] == []
    assert json.loads((storage.data_dir() / "settings.json").read_text())["exclusions"] == 5


def test_a_scheduled_run_keeps_schedule_changes_made_while_it_ran(monkeypatch):
    storage.update_settings(schedule={"enabled": True, "paths": ["C:/a"], "frequency": "weekly"})

    def user_turns_it_off(*args, **kwargs):
        storage.update_settings(schedule={"enabled": False, "paths": ["C:/b"], "frequency": "daily"})
        return {}

    monkeypatch.setattr(scheduler, "run_report", user_turns_it_off)
    scheduler.run_scheduled_report()
    schedule = storage.load_settings()["schedule"]
    assert (schedule["enabled"], schedule["paths"], schedule["frequency"]) == (False, ["C:/b"], "daily")
    assert schedule["last_run"]


def test_personal_files_inside_an_unsure_folder_ask_for_delete_typed(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "wedding.jpg").write_bytes(b"x")
    offer([out], is_dir=True, risk_level="Review Recommended")
    body = {"items": [{"path": str(out)}], "permanent": True}
    assert client.post("/api/clean", json=body).status_code == 400
    assert (out / "wedding.jpg").exists()
    preview = client.post("/api/delete-preview", json={"paths": [str(out)], "permanent": True}).json()
    assert preview["needs_typed_confirm"] and preview["personal_examples"] == ["wedding.jpg"]


def test_windows_folder_with_an_open_file_is_left_whole(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "a.tmp").write_text("x")
    (cache / "b.tmp").write_text("x")
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)

    def busy(src, dst):
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(cleaner.os, "rename", busy)
    result = cleaner.delete_items([{"path": str(cache), "is_directory": True, "size_bytes": 2}], permanent=True)
    assert result.deleted_count == 0 and "open in another program" in result.errors[0]["error"]
    assert sorted(os.listdir(cache)) == ["a.tmp", "b.tmp"]
