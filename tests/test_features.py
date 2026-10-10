"""Duplicates, exclusions, junk locations, history and scheduled reports."""
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import CAT_DUPLICATES, CAT_TEMP_JUNK, ScanOptions
from app.engine import scheduler, storage
from app.engine.exclusions import ExclusionMatcher
from app.engine.locations import JunkLocation, windows_junk_locations
from app.engine.scanner import FastScanner
from app.main import app

MB = 1024 * 1024
client = TestClient(app, base_url="http://127.0.0.1")


def _scan(**kwargs):
    scanner = FastScanner(ScanOptions(**kwargs))
    return scanner.run_scan()


# ------------------------------------------------------------- Duplicates

def test_duplicates_flag_every_copy_but_the_newest(tmp_path):
    data = os.urandom(MB + 10)
    old, new, other = tmp_path / "a" / "old.dat", tmp_path / "b" / "new.dat", tmp_path / "other.dat"
    for p in (old, new):
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(data)
    other.write_bytes(data[:-1] + b"X")  # same size, different content
    os.utime(old, (time.time() - 1000, time.time() - 1000))

    items = _scan(target_path=str(tmp_path), include_duplicates=True)
    dups = [i for i in items if i.category == CAT_DUPLICATES]

    assert [d.path for d in dups] == [str(old)]
    assert str(new) in dups[0].reason
    assert dups[0].selected is False


def test_duplicates_off_by_default_and_small_files_ignored(tmp_path):
    (tmp_path / "x.dat").write_bytes(b"same" * 10)
    (tmp_path / "y.dat").write_bytes(b"same" * 10)
    assert _scan(target_path=str(tmp_path), include_duplicates=True) == []
    big = os.urandom(MB)
    (tmp_path / "p.dat").write_bytes(big)
    (tmp_path / "q.dat").write_bytes(big)
    assert _scan(target_path=str(tmp_path)) == []


def test_hard_links_are_not_duplicates(tmp_path):
    data = os.urandom(MB)
    (tmp_path / "one.dat").write_bytes(data)
    try:
        os.link(tmp_path / "one.dat", tmp_path / "two.dat")
    except OSError:
        pytest.skip("hard links not supported")
    assert _scan(target_path=str(tmp_path), include_duplicates=True) == []


# ------------------------------------------------------------- Exclusions

def test_exclusion_matcher_paths_and_patterns():
    m = ExclusionMatcher(["C:\\Projects\\keep", "*.iso", "  "])
    assert m.matches("c:/projects/keep")
    assert m.matches("C:\\Projects\\keep\\build\\x.tmp")
    assert not m.matches("C:\\Projects\\keeper\\x.tmp")
    assert m.matches("D:\\Downloads\\Win11.ISO")
    assert not ExclusionMatcher([]).matches("anything")


def test_scanner_skips_excluded_folders_and_patterns(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "a.tmp").write_text("x")
    (tmp_path / "b.log").write_text("x")
    (tmp_path / "c.tmp").write_text("x")

    items = _scan(target_path=str(tmp_path), exclusions=[str(tmp_path / "keep"), "*.log"])

    assert [i.name for i in items] == ["c.tmp"]


def test_excluded_folder_is_not_reported_empty(tmp_path):
    (tmp_path / "placeholder").mkdir()
    items = _scan(target_path=str(tmp_path), exclusions=[str(tmp_path / "placeholder")])
    assert items == []


def test_exclusions_api_round_trip():
    assert client.get("/api/exclusions").json() == {"rules": []}
    client.put("/api/exclusions", json={"rules": ["*.iso", "*.iso", " C:\\keep "]})
    client.post("/api/exclusions/add", json={"rule": "D:\\VMs"})
    assert client.get("/api/exclusions").json()["rules"] == ["*.iso", "C:\\keep", "D:\\VMs"]


# ------------------------------------------------------------- Junk locations

def _make_locations(tmp_path):
    local = tmp_path / "Local"
    (local / "Temp").mkdir(parents=True)
    (local / "CrashDumps").mkdir()
    (local / "Google" / "Chrome" / "User Data" / "Default" / "Cache").mkdir(parents=True)
    env = {"LOCALAPPDATA": str(local), "TEMP": str(local / "Temp")}
    return local, windows_junk_locations(env)


def test_windows_junk_locations_only_lists_existing_folders(tmp_path):
    local, locations = _make_locations(tmp_path)
    ids = {loc.id for loc in locations}
    assert ids == {"user_temp", "crash_dumps", "chrome:default:cache"}
    assert windows_junk_locations({}) == []


def test_junk_location_scan_flags_temp_files_and_cache_folders(tmp_path):
    local, locations = _make_locations(tmp_path)
    old = time.time() - 3 * 86400
    temp_file = local / "Temp" / "setup123.dat"
    temp_file.write_text("x")
    os.utime(temp_file, (old, old))
    (local / "Temp" / "fresh.dat").write_text("x")
    (local / "CrashDumps" / "app.exe.1234.dmp").write_text("x")
    (local / "Google" / "Chrome" / "User Data" / "Default" / "Cache" / "f_0001").write_text("cache")

    items = _scan(target_path="", junk_locations=locations, scan_junk_locations=True)
    by_name = {i.name: i for i in items}

    assert by_name["setup123.dat"].category == CAT_TEMP_JUNK
    assert by_name["setup123.dat"].risk_level == "Safe"
    assert by_name["fresh.dat"].risk_level == "Review Recommended"
    assert by_name["app.exe.1234.dmp"].category == CAT_TEMP_JUNK
    assert by_name["Cache"].is_directory and by_name["Cache"].size_bytes == 5
    assert "f_0001" not in by_name


def test_cache_folder_inside_target_is_listed_once(tmp_path):
    local, locations = _make_locations(tmp_path)
    items = _scan(target_path=str(tmp_path), junk_locations=locations, scan_junk_locations=True,
                  include_empty_folders=False)
    assert [i.name for i in items if i.name == "Cache"] == ["Cache"]


def test_junk_locations_endpoint_and_location_only_scan():
    assert "locations" in client.get("/api/system/junk-locations").json()
    assert client.post("/api/scan/start", json={"target_path": ""}).status_code == 400


# ------------------------------------------------------------- History

def test_cleanup_is_recorded_in_history(tmp_path):
    junk = tmp_path / "junk.tmp"
    junk.write_text("12345")
    missing = tmp_path / "missing.tmp"

    res = client.post("/api/clean", json={"permanent": True, "items": [
        {"path": str(junk), "size_bytes": 5}, {"path": str(missing), "size_bytes": 1}]})
    assert res.json()["deleted_count"] == 1

    history = client.get("/api/history").json()["history"]
    assert len(history) == 1
    assert history[0]["paths"] == [str(junk)]
    assert history[0]["mode"] == "permanent"
    assert history[0]["freed_bytes"] == 5

    client.delete("/api/history")
    assert client.get("/api/history").json()["history"] == []


def test_failed_cleanup_is_not_recorded(tmp_path):
    client.post("/api/clean", json={"items": [{"path": str(tmp_path / "nope"), "size_bytes": 0}]})
    assert storage.load_history() == []


# ------------------------------------------------------------- Scheduled reports

def test_schtasks_arguments():
    daily = scheduler.schtasks_create_args("daily", "08:30", "MON", r"C:\d\s.pyw", r"C:\py\pythonw.exe")
    assert daily[daily.index("/SC") + 1] == "DAILY"
    assert daily[daily.index("/TR") + 1] == '"C:\\py\\pythonw.exe" "C:\\d\\s.pyw"'
    weekly = scheduler.schtasks_create_args("weekly", "08:30", "FRI", "s", "p")
    assert weekly[-3:] == ["WEEKLY", "/D", "FRI"]


@pytest.mark.parametrize("args", [
    ("hourly", "09:00", "MON", [], True),
    ("daily", "9am", "MON", [], True),
    ("weekly", "09:00", "XYZ", [], True),
    ("daily", "09:00", "MON", [], False),
    ("daily", "09:00", "MON", ["/definitely/missing"], True),
])
def test_invalid_schedules_rejected(args):
    with pytest.raises(scheduler.ScheduleError):
        scheduler.validate_schedule(*args)


def test_save_schedule_registers_task(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "_run_schtasks", calls.append)
    schedule = scheduler.save_schedule("weekly", "07:15", "SUN", [str(tmp_path)], False)
    assert schedule["enabled"] and schedule["paths"] == [str(tmp_path)]
    assert calls[0][:5] == ["schtasks", "/Create", "/F", "/TN", scheduler.TASK_NAME]
    script = storage.data_dir() / "scheduled_scan.pyw"
    compile(script.read_text(), str(script), "exec")

    scheduler.delete_schedule()
    assert scheduler.get_schedule()["enabled"] is False


def test_scheduled_report_never_deletes(tmp_path):
    junk = tmp_path / "junk.tmp"
    junk.write_text("x")
    storage.update_settings(schedule={"paths": [str(tmp_path)], "include_junk_locations": False})

    assert scheduler.run_scheduled_report() == 0

    assert junk.exists()
    report = client.get("/api/reports/latest").json()
    assert report["item_count"] == 1 and report["items"][0]["path"] == str(junk)
    status = client.get("/api/schedule").json()
    assert status["schedule"]["last_run"] and "items" not in status["last_report"]
