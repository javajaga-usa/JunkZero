"""Round 9 backend fixes: full cleanup history, scan stream, batched deletes with progress and
cancel, schedules, Gemini, updates, diagnostics, folder picker, closing and single instance."""
import json
import os
import struct
import threading
import time
import urllib.error
from types import SimpleNamespace

import pytest

from app import __version__, main
from app.config import CAT_DUPLICATES, HOLDING_DIR_NAME
from app.engine import ai_advisor, cleaner, osinfo, recycle, scheduler, storage
from tests.helpers import api_client, offer

DAY = 86400


# ------------------------------------------------------------- 2. Every path of a cleanup is kept

def _held_cleanup(tmp_path, count):
    batch = recycle.new_holding_batch(str(tmp_path))
    held, paths = {}, []
    for n in range(count):
        original = str(tmp_path / "usb" / f"f{n}.tmp")
        stored = os.path.join(batch, f"f{n}.tmp")
        with open(stored, "w") as f:
            f.write(str(n))
        held[original] = stored
        paths.append(original)
    os.makedirs(tmp_path / "usb", exist_ok=True)
    result = {"deleted_count": count, "mode": "recycle_bin", "held": held, "errors": []}
    return storage.record_cleanup(result, paths, "test"), paths, batch


def test_big_cleanups_are_fully_restored(tmp_path):
    record, paths, _ = _held_cleanup(tmp_path, 650)
    shown = storage.load_history()[0]
    assert len(shown["paths"]) == storage.HISTORY_PATHS_LIMIT and shown["paths_truncated"] == 150
    assert shown["path_count"] == 650 and len(shown["held"]) == storage.HISTORY_PATHS_LIMIT

    full = storage.find_cleanup(record["timestamp"])
    assert len(full["paths"]) == 650 and len(full["held"]) == 650
    out = api_client().post("/api/history/restore", json={"timestamp": record["timestamp"]}).json()
    assert len(out["restored"]) == 650 and out["errors"] == []
    assert all(os.path.exists(p) for p in paths)
    assert out["history"][0]["restored_count"] == 650
    assert len(out["history"][0]["restored"]) == storage.HISTORY_PATHS_LIMIT


def test_old_history_records_still_restore(tmp_path):
    stored = tmp_path / "held" / "a.txt"
    stored.parent.mkdir()
    stored.write_text("a")
    original = tmp_path / "a.txt"
    old = {"timestamp": 1000.0, "mode": "recycle_bin", "paths": [str(original)],
           "held": {str(original): str(stored)}, "restored": []}
    storage.write_json("history.json", [old])
    out = api_client().post("/api/history/restore", json={"timestamp": 1000.0}).json()
    assert out["restored"] == [str(original)] and original.read_text() == "a"


def test_record_cleanup_skips_failed_paths_and_keeps_errors():
    errors = [{"path": f"/bad{n}", "error": "locked"} for n in range(250)]
    record = storage.record_cleanup({"deleted_count": 2, "errors": errors},
                                    ["/ok1"] + [e["path"] for e in errors] + ["/ok2"], "test")
    assert record["paths"] == ["/ok1", "/ok2"]
    assert len(record["errors"]) == storage.HISTORY_ERRORS_LIMIT and record["errors_total"] == 250
    assert record["errors"][0] == {"path": "/bad0", "error": "locked"}


def test_held_items_keep_their_record_after_history_is_cleared(tmp_path, monkeypatch):
    real_time = time.time
    monkeypatch.setattr(storage.time, "time", lambda: real_time() - 8 * DAY)
    record, _, batch = _held_cleanup(tmp_path, 3)
    monkeypatch.setattr(storage.time, "time", real_time)
    storage.clear_history()
    # The cleanup's own file is still the record of the held items, so they go after a week
    assert os.listdir(storage.data_dir() / storage.CLEANUPS_DIR)
    assert recycle.purge_holding(now=real_time() + 8 * DAY) == 1
    assert not os.path.exists(batch)
    assert os.listdir(storage.data_dir() / storage.CLEANUPS_DIR) == []


def test_unrecorded_holding_batches_are_never_erased(tmp_path):
    batch = recycle.new_holding_batch(str(tmp_path), now=time.time() - 30 * DAY)
    assert recycle.purge_holding() == 0 and os.path.isdir(batch)
    assert HOLDING_DIR_NAME in batch


# ------------------------------------------------------------- 8, 9, 14. Scan stream

def _stream_scan(tmp_path, monkeypatch, run_scan=None, compare=None):
    if run_scan is not None:
        monkeypatch.setattr(main.FastScanner, "run_scan", run_scan)
    if compare is not None:
        monkeypatch.setattr(main.changes, "compare_and_remember", compare)
    with api_client() as session:
        assert session.post("/api/scan/start", json={"target_path": str(tmp_path)}).status_code == 200
        body = session.get("/api/scan/stream", params={"token": main.API_TOKEN}).text
        status = session.get("/api/scan/status").json()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    return events, status


def test_finished_message_arrives_even_when_saving_the_comparison_is_slow(tmp_path, monkeypatch):
    (tmp_path / "a.tmp").write_text("x")

    def slow_compare(options, items):
        time.sleep(1.6)  # Longer than the old one-second stream timeout
        return {"new_count": 0}

    events, status = _stream_scan(tmp_path, monkeypatch, compare=slow_compare)
    assert events[-1]["type"] == "completed" and events[-1]["error"] == ""
    assert events[-1]["changes"] == {"new_count": 0}
    assert status["is_running"] is False and status["status"] == "finished"


def test_progress_is_sent_while_nothing_is_found(tmp_path, monkeypatch):
    def slow_scan(self):
        self.stats.is_running = True
        self.stats.start_time = time.time()
        time.sleep(1.0)
        self.stats.is_running, self.stats.is_completed = False, True
        return []

    events, _ = _stream_scan(tmp_path, monkeypatch, run_scan=slow_scan)
    progress = [e for e in events if e["type"] == "progress"]
    assert len(progress) >= 3
    assert set(progress[0]["stats"]) == {"files_scanned", "dirs_scanned", "garbage_count", "garbage_bytes",
                                         "elapsed_seconds", "phase"}
    assert progress[0]["stats"]["phase"]
    assert events[-1]["type"] == "completed"


def test_stream_ends_for_results_that_are_not_a_running_scan(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(main, "event_queue", asyncio.Queue())
    offer([tmp_path / "a.tmp"])
    with api_client() as session:
        assert session.get("/api/scan/stream", params={"token": main.API_TOKEN}).status_code == 200


def test_a_scan_that_fails_still_finishes_with_the_error(tmp_path, monkeypatch):
    def broken(self):
        raise RuntimeError("disk went away")

    events, status = _stream_scan(tmp_path, monkeypatch, run_scan=broken)
    assert events[-1]["type"] == "completed" and "disk went away" in events[-1]["error"]
    assert status["is_running"] is False and "error" in status and "phase" in status


# ------------------------------------------------------------- 11. Batched deletes, progress, cancel

def _files(tmp_path, count):
    paths = []
    for n in range(count):
        p = tmp_path / f"j{n}.tmp"
        p.write_text("x")
        paths.append(str(p))
    return paths


def test_recycle_bin_gets_items_in_batches(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cleaner.send2trash, "send2trash", calls.append)
    paths = _files(tmp_path, 250)
    result = cleaner.delete_items([{"path": p, "size_bytes": 1} for p in paths])
    assert [len(c) for c in calls] == [100, 100, 50] and result.deleted_count == 250
    assert cleaner.delete_progress() == {"running": False, "done": 250, "total": 250}


def test_a_failed_batch_is_retried_one_by_one(tmp_path, monkeypatch):
    paths = _files(tmp_path, 5)
    bad = paths[2]

    def trash(arg):
        if isinstance(arg, list) or arg == bad:
            raise OSError("access denied")
        os.remove(arg)

    monkeypatch.setattr(cleaner.send2trash, "send2trash", trash)
    result = cleaner.delete_items([{"path": p, "size_bytes": 1} for p in paths])
    assert result.deleted_count == 4 and result.failed_count == 1
    # macOS adds a hint about the Trash to the message
    assert [e["path"] for e in result.errors] == [bad] and "access denied" in result.errors[0]["error"]


def test_cancel_stops_after_the_current_batch(tmp_path, monkeypatch):
    paths = _files(tmp_path, 250)
    offer(paths)
    client = api_client()

    def trash(batch):
        assert client.post("/api/clean/cancel").json() == {"status": "cancelling"}
        progress = client.get("/api/clean/progress").json()
        assert progress["running"] is True and progress["total"] == 250

    monkeypatch.setattr(cleaner.send2trash, "send2trash", trash)
    res = client.post("/api/clean", json={"items": [{"path": p} for p in paths]}).json()
    assert res["deleted_count"] == 100 and res["skipped_count"] == 150 and res["failed_count"] == 0
    assert storage.load_history()[0]["path_count"] == 100
    assert client.post("/api/clean/cancel").json() == {"status": "not_running"}
    assert client.get("/api/clean/progress").json()["running"] is False


def test_drive_settings_are_looked_up_once_per_drive(tmp_path, monkeypatch):
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    asked = {"type": 0, "settings": 0}

    def drive_type(root):
        asked["type"] += 1
        return 3

    def settings(root):
        asked["settings"] += 1
        return 10 ** 9, False

    monkeypatch.setattr(recycle, "_windows_drive_type", drive_type)
    monkeypatch.setattr(recycle, "_windows_recycle_settings", settings)
    with recycle.per_drive_lookups():
        assert all(recycle.recycle_bin_takes(f"E:\\junk\\{n}.tmp", 10) for n in range(50))
    assert asked == {"type": 1, "settings": 1}


def _i_file(path, when):
    filetime = int((when + 11644473600) * 1e7)
    raw = (path + "\0").encode("utf-16-le")
    return struct.pack("<qqqi", 2, 5, filetime, len(path) + 1) + raw


def test_restore_reads_the_recycle_bin_once(tmp_path, monkeypatch):
    drive = tmp_path / "C"
    user_bin = drive / "$Recycle.Bin" / "S-1-5-21"
    user_bin.mkdir(parents=True)
    originals = [str(drive / "work" / f"{n}.tmp") for n in range(5)]
    for n, original in enumerate(originals):
        (user_bin / f"$IX{n}.tmp").write_bytes(_i_file(original, time.time()))
        (user_bin / f"$RX{n}.tmp").write_text(str(n))
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    monkeypatch.setattr(recycle, "mount_point", lambda p: str(drive))
    reads = []
    real_index = recycle.windows_recycle_index
    monkeypatch.setattr(recycle, "windows_recycle_index", lambda root: reads.append(root) or real_index(root))
    out = recycle.restore_cleanup({"mode": "recycle_bin", "timestamp": time.time(), "paths": originals})
    assert sorted(out["restored"]) == sorted(originals) and len(reads) == 1
    assert open(originals[3]).read() == "3"


# ------------------------------------------------------------- 12. Duplicate check before deleting

def test_same_content_compares_samples_and_whole_files(tmp_path):
    size = 300 * 1024
    a, b, c = tmp_path / "a.bin", tmp_path / "b.bin", tmp_path / "c.bin"
    a.write_bytes(b"\0" * size)
    b.write_bytes(b"\0" * size)
    middle = bytearray(size)
    middle[size // 2] = 1  # Differs only in the middle: the samples match, the full check doesn't
    c.write_bytes(bytes(middle))
    assert cleaner.same_content(str(a), str(b))
    assert not cleaner.same_content(str(a), str(c))
    assert not cleaner.same_content(str(a), str(a))


def test_the_kept_copy_is_read_once_for_all_its_duplicates(tmp_path, monkeypatch):
    size = 300 * 1024
    keeper = tmp_path / "keep.bin"
    keeper.write_bytes(b"k" * size)
    copies = []
    for n in range(3):
        copy = tmp_path / f"copy{n}.bin"
        copy.write_bytes(b"k" * size)
        copies.append(copy)
    hashed = []
    real_sha = cleaner.hashlib.sha256

    def counting_sha():
        hashed.append(1)
        return real_sha()

    monkeypatch.setattr(cleaner.hashlib, "sha256", counting_sha)
    items = [{"path": str(c), "size_bytes": size, "category": CAT_DUPLICATES, "duplicate_paths": [str(keeper)]}
             for c in copies]
    result = cleaner.delete_items(items, permanent=True)
    assert result.deleted_count == 3 and keeper.exists()
    assert len(hashed) == 4  # The keeper once, each copy once


# ------------------------------------------------------------- 10. Scheduled scans on Windows

def test_task_runs_on_battery_and_makes_up_missed_runs():
    xml = scheduler.task_xml("weekly", "09:00", "MON", r"C:\Apps\JunkZero.exe", "--mode report")
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml
    assert r"<Command>C:\Apps\JunkZero.exe</Command>" in xml and "<Monday />" in xml


def _windows(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    monkeypatch.setattr(scheduler, "_task_scheduler", lambda: True)


def test_turning_off_a_task_that_is_gone_works_in_any_language(monkeypatch):
    _windows(monkeypatch)
    storage.update_settings(schedule={"enabled": True})

    def fails(args, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="FEHLER: Das System kann die Datei nicht finden.")

    monkeypatch.setattr(scheduler.subprocess, "run", fails)
    assert scheduler.delete_schedule()["enabled"] is False


def test_a_task_that_is_still_there_after_a_failed_delete_is_an_error(monkeypatch):
    _windows(monkeypatch)
    storage.update_settings(schedule={"enabled": True})

    def run(args, **kw):
        if "/Delete" in args:
            return SimpleNamespace(returncode=1, stdout="", stderr="Zugriff verweigert")
        return SimpleNamespace(returncode=0, stdout="<Command>C:\\x.exe</Command>", stderr="")

    monkeypatch.setattr(scheduler.subprocess, "run", run)
    with pytest.raises(scheduler.ScheduleError):
        scheduler.delete_schedule()


def test_startup_points_the_task_at_the_current_exe(monkeypatch, tmp_path):
    _windows(monkeypatch)
    monkeypatch.setattr(scheduler.sys, "frozen", True, raising=False)
    monkeypatch.setattr(scheduler.sys, "executable", r"C:\New\JunkZero.exe")
    storage.update_settings(schedule={"enabled": True, "frequency": "daily", "time": "09:00", "day": "MON"})
    created = []
    query = {"stdout": "<Exec><Command>C:\\Old\\JunkZero.exe</Command></Exec>"}

    def run(args, **kw):
        if "/Create" in args:
            xml_path = args[args.index("/XML") + 1]
            created.append(open(xml_path, encoding="utf-16").read())
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout=query["stdout"], stderr="")

    monkeypatch.setattr(scheduler.subprocess, "run", run)
    assert scheduler.ensure_task_current() is True
    assert r"<Command>C:\New\JunkZero.exe</Command>" in created[0]
    query["stdout"] = "<Exec><Command>C:\\New\\JunkZero.exe</Command></Exec>"
    assert scheduler.ensure_task_current() is False and len(created) == 1


def test_failed_scheduled_runs_are_recorded(monkeypatch):
    storage.update_settings(schedule={"enabled": True, "paths": [], "include_junk_locations": True})

    def broken(*a, **k):
        raise RuntimeError("no space left")

    monkeypatch.setattr(scheduler, "run_report", broken)
    assert scheduler.run_scheduled_report() == 1
    error = api_client().get("/api/schedule").json()["schedule"]["last_error"]
    assert "no space left" in error["error"] and error["at"]


# ------------------------------------------------------------- 24. Gemini

def test_gemini_missing_from_the_build_says_so(tmp_path, monkeypatch):
    f = tmp_path / "a.tmp"
    f.write_text("x")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setattr(ai_advisor, "_gemini_available", lambda: False)
    result = ai_advisor.analyze_item(str(f))
    assert result.ai_powered is False and "not available in this build" in result.note


def test_gemini_is_never_more_confident_than_junkzero(tmp_path, monkeypatch):
    f = tmp_path / "disk.iso"  # JunkZero's own verdict: Review Carefully
    f.write_text("x")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setattr(ai_advisor, "_gemini_available", lambda: True)
    monkeypatch.setattr(ai_advisor, "_ask_gemini", lambda *a: {"safety_verdict": "Safe to Delete",
                                                               "detected_type": "Temp", "recommendation": "Delete"})
    result = ai_advisor.analyze_item(str(f))
    assert result.ai_powered and result.safety_verdict == "Review Carefully" and result.note


def test_gemini_that_does_not_answer_falls_back(tmp_path, monkeypatch):
    f = tmp_path / "a.zip"
    f.write_text("x")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setattr(ai_advisor, "_gemini_available", lambda: True)
    monkeypatch.setattr(ai_advisor, "GEMINI_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(ai_advisor, "_ask_gemini", lambda *a: time.sleep(2) or {})
    started = time.time()
    result = ai_advisor.analyze_item(str(f))
    assert time.time() - started < 1.5 and result.ai_powered is False and "did not answer" in result.note


# ------------------------------------------------------------- 21. Check for updates

class _Answer:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def read(self, *a):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_update_check_reports_a_newer_release(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen.update(url=request.full_url, timeout=timeout)
        return _Answer({"tag_name": "v99.0.0", "html_url": "https://github.com/javajaga-usa/JunkZero/releases/tag/v99.0.0"})

    monkeypatch.setattr(main.urllib.request, "urlopen", urlopen)
    res = api_client().get("/api/update/check").json()
    assert res == {"current": __version__, "latest": "99.0.0", "newer": True,
                   "url": "https://github.com/javajaga-usa/JunkZero/releases/tag/v99.0.0", "error": ""}
    assert seen == {"url": main.RELEASES_API, "timeout": 5}


def test_update_check_same_version_and_offline(monkeypatch):
    monkeypatch.setattr(main.urllib.request, "urlopen", lambda r, timeout: _Answer({"tag_name": __version__}))
    assert api_client().get("/api/update/check").json()["newer"] is False

    def offline(request, timeout):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(main.urllib.request, "urlopen", offline)
    res = api_client().get("/api/update/check").json()
    assert res["latest"] is None and res["newer"] is False and "no network" in res["error"]


def test_update_check_needs_the_token():
    assert api_client(headers={main.TOKEN_HEADER: "wrong"}).get("/api/update/check").status_code == 403


# ------------------------------------------------------------- 17. Diagnostics

def test_diagnostics_text(monkeypatch):
    main.setup_logging()
    main.logger.warning("diagnostics marker line")
    for h in main.logging.getLogger().handlers:
        h.flush()
    text = api_client().get("/api/diagnostics").json()["text"]
    assert f"JunkZero {__version__}" in text and "Python:" in text and "Data folder:" in text
    assert "diagnostics marker line" in text
    root = main.logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "baseFilename", "").endswith("junkzero.log")]:
        root.removeHandler(h)
        h.close()


def test_logs_rotate():
    handler = [h for h in cleaner.logger.handlers if hasattr(h, "maxBytes")]
    assert handler and handler[0].maxBytes == 1024 * 1024 and handler[0].backupCount == 3


# ------------------------------------------------------------- 15. Folder picker on Windows

def test_windows_uses_the_window_folder_dialog_first(monkeypatch, tmp_path):
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    monkeypatch.setattr(main, "_window_folder_dialog", lambda start: str(tmp_path))
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: pytest.fail("no PowerShell when the window can"))
    assert api_client().post("/api/system/browse-folder").json() == {"path": str(tmp_path)}


def test_powershell_picker_has_no_time_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    monkeypatch.setattr(main, "gui_window", None)
    seen = {}

    def run(args, **kw):
        seen.update(kw)
        return SimpleNamespace(stdout=str(tmp_path) + "\n", returncode=0)

    monkeypatch.setattr(main.subprocess, "run", run)
    assert api_client().post("/api/system/browse-folder").json() == {"path": str(tmp_path)}
    assert "timeout" not in seen


# ------------------------------------------------------------- 18. Closing mid-delete, one JunkZero

def test_window_stays_open_while_deleting(monkeypatch):
    shown = []
    monkeypatch.setattr(main, "gui_window", SimpleNamespace(evaluate_js=shown.append))
    monkeypatch.setattr(cleaner, "delete_running", lambda: True)
    assert main.on_window_closing() is False and "still deleting" in shown[0]
    monkeypatch.setattr(cleaner, "delete_running", lambda: False)
    assert main.on_window_closing() is True


@pytest.mark.skipif(os.name == "nt", reason="the lock file is for macOS and Linux")
def test_only_one_junkzero_at_a_time(monkeypatch):
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(main, "_instance_handle", None)
    assert main.acquire_single_instance() is True
    first = main._instance_handle
    try:
        assert main.acquire_single_instance() is False
    finally:
        first.close()


def test_second_launch_asks_the_first_to_show_its_window(monkeypatch):
    calls = []
    window = SimpleNamespace(restore=lambda: calls.append("restore"), show=lambda: calls.append("show"))
    monkeypatch.setattr(main, "gui_window", window)
    # Needs no token (it reveals nothing), but other web pages still can't call it
    bare = api_client(headers={main.TOKEN_HEADER: ""})
    assert bare.post("/api/instance/focus").json() == {"focused": True} and calls == ["restore", "show"]
    assert bare.post("/api/instance/focus", headers={"Origin": "http://evil.example"}).status_code == 403


# ------------------------------------------------------------- System cleanup hints

def test_cleanup_hints(monkeypatch):
    monkeypatch.delattr(main.locations, "system_cleanup_hints", raising=False)
    assert api_client().get("/api/system/cleanup-hints").json() == {"hints": []}
    monkeypatch.setattr(main.locations, "system_cleanup_hints", lambda: [{"name": "Windows Update"}], raising=False)
    assert api_client().get("/api/system/cleanup-hints").json() == {"hints": [{"name": "Windows Update"}]}


def test_full_lists_saved_before_round_9_are_still_used(tmp_path):
    # Records saved with "full_details" keep their full list in cleanups/<timestamp with 3 decimals>.json
    ts = 1792000000.25
    storage.write_json("history.json", [{"timestamp": ts, "source": "test", "paths": ["a"], "full_details": True}])
    (storage.data_dir() / storage.CLEANUPS_DIR).mkdir(parents=True, exist_ok=True)
    storage.write_json(f"{storage.CLEANUPS_DIR}/{ts:.3f}.json", {"paths": ["a", "b", "c"], "held": {}, "trash_ids": {}})
    assert storage.find_cleanup(ts)["paths"] == ["a", "b", "c"]
    storage.clear_history()
    assert os.listdir(storage.data_dir() / storage.CLEANUPS_DIR) == []
