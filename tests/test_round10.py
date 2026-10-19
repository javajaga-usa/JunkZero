"""Round 10: space still held in the Recycle Bin, notifications, app caches, duplicate folders,
old coding projects, installers for installed programs, and the free-space log."""
import os
import sys
import time

import pytest

from app import main
from app.config import CAT_DUPLICATE_FOLDERS, CAT_INSTALLERS, CAT_OLD_PROJECTS, HOLDING_DIR_NAME, RISK_REVIEW, ScanOptions
from app.engine import appcaches, cleaner, dupfolders, installed, notify, projects, reclaim, spacelog, storage
from app.engine import smart
from app.engine.classifier import classify_item
from app.engine.duplicates import same_content
from app.engine.scanner import FastScanner
from exe_samples import make_exe, version_info
from helpers import api_client, offer

OLD = time.time() - 400 * 86400
MB = 1024 * 1024


def _write(path, data=b"x", mtime=OLD):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _age_tree(folder, mtime=OLD):
    for root, dirs, files in os.walk(folder):
        for name in dirs + files:
            os.utime(os.path.join(root, name), (mtime, mtime))
    os.utime(folder, (mtime, mtime))


def _scan(target, **kw):
    kw.setdefault("leftovers", [])
    kw.setdefault("cloud_folders", [])
    kw.setdefault("include_empty_folders", False)
    s = FastScanner(ScanOptions(target_path=str(target), **kw))
    items = s.run_scan()
    return s, {os.path.relpath(i.path, target).replace(os.sep, "/"): i for i in items}


# ------------------------------------------------------------- 1. Space still held

def _held_cleanup(tmp_path, name="video.bin", size=4096, age_days=10):
    """A cleanup whose item sits in a holding folder, recorded age_days ago."""
    original = str(tmp_path / "usb" / name)
    batch = tmp_path / "usb" / HOLDING_DIR_NAME / "20260101-120000-abcdef"
    held = batch / name
    _write(held, b"v" * size)
    record = storage.record_cleanup({"mode": "recycle_bin", "deleted_count": 1, "freed_bytes": size,
                                     "held": {original: str(held)}}, [original], "manual")
    when = time.time() - age_days * 86400
    history = storage.load_history()
    history[0]["timestamp"] = when
    history[0]["started_at"] = when
    storage._write_json("history.json", history)
    full = storage._read_cleanup_file(record)
    full["timestamp"] = when
    storage._write_cleanup_file(when, full)
    history[0]["full_record"] = storage._cleanup_file(when).name
    storage._write_json("history.json", history)
    return original, held, batch, when


def test_space_still_held_counts_and_empties_only_old_items(tmp_path):
    original, held, batch, when = _held_cleanup(tmp_path)
    s = reclaim.summary(7)
    assert s["junkzero_count"] == 1 and s["junkzero_bytes"] == 4096 and s["holding_bytes"] == 4096
    assert s["old_count"] == 1 and s["old_bytes"] == 4096
    # Newer than 30 days: counted, but not old enough to empty
    assert reclaim.summary(30)["old_count"] == 0
    assert reclaim.empty_old(30)["emptied"] == 0 and held.exists()

    result = reclaim.empty_old(7)
    assert result["emptied"] == 1 and result["freed_bytes"] == 4096 and not result["errors"]
    assert not held.exists() and not batch.exists()
    record = storage.find_cleanup(when)
    assert record["emptied"] == [original]
    assert storage.load_history()[0]["emptied_count"] == 1
    assert reclaim.summary(7)["junkzero_count"] == 0


def test_emptied_items_are_not_restored(tmp_path):
    original, held, _batch, when = _held_cleanup(tmp_path)
    reclaim.empty_old(7)
    from app.engine import recycle
    result = recycle.restore_cleanup(storage.find_cleanup(when))
    assert not result.get("restored") and not os.path.exists(original)


def test_items_moved_out_of_the_holding_folder_are_never_touched(tmp_path):
    original = str(tmp_path / "docs" / "a.txt")
    elsewhere = _write(tmp_path / "docs" / "elsewhere.txt", b"keep")
    storage.record_cleanup({"mode": "recycle_bin", "deleted_count": 1, "held": {original: str(elsewhere)}},
                           [original], "manual")
    assert reclaim.summary(0)["junkzero_count"] == 0
    assert reclaim.empty_old(0, now=time.time() + 86400)["emptied"] == 0
    assert elsewhere.exists()


def test_permanent_cleanups_hold_nothing(tmp_path):
    storage.record_cleanup({"mode": "permanent", "deleted_count": 1}, [str(tmp_path / "x")], "manual")
    assert reclaim.summary(0)["junkzero_count"] == 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="freedesktop.org Trash")
def test_trash_items_match_only_the_cleanup_that_moved_them(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    trash = tmp_path / "share" / "Trash"
    original = str(tmp_path / "home" / "big.iso")
    started = time.time() - 3600

    def trashed(name, deleted_at):
        _write(trash / "files" / name, b"i" * 1000, mtime=None)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(deleted_at))
        (trash / "info").mkdir(parents=True, exist_ok=True)
        (trash / "info" / f"{name}.trashinfo").write_text(f"[Trash Info]\nPath={original}\nDeletionDate={stamp}\n")

    storage.record_cleanup({"mode": "recycle_bin", "deleted_count": 1, "started_at": started}, [original], "manual")
    history = storage.load_history()
    history[0]["timestamp"] = started + 5
    storage._write_json("history.json", history)
    # Deleted again a day later by something else: not JunkZero's item
    trashed("big.iso", started + 86400)
    assert reclaim.summary(0, now=time.time() + 86400)["junkzero_count"] == 0
    trashed("big.2.iso", started + 2)
    s = reclaim.summary(0, now=time.time() + 86400)
    assert s["junkzero_count"] == 1 and s["trash_bytes"] == 1000
    reclaim.empty_old(0, now=time.time() + 86400)
    assert not (trash / "files" / "big.2.iso").exists() and not (trash / "info" / "big.2.iso.trashinfo").exists()
    assert (trash / "files" / "big.iso").exists()


def test_reclaim_api(tmp_path):
    _held_cleanup(tmp_path)
    client = api_client()
    data = client.get("/api/reclaim?days=7").json()
    assert data["old_count"] == 1
    res = client.post("/api/reclaim/empty", json={"older_than_days": 7}).json()
    assert res["emptied"] == 1 and res["summary"]["junkzero_count"] == 0
    assert res["history"][0]["emptied_count"] == 1


# ------------------------------------------------------------- 2. Notifications

def _report(total_mb=800, source="scheduled", at=None):
    return {"source": source, "generated_at": at or time.time(), "total_bytes": total_mb * MB, "item_count": 3,
            "items": [{"size_bytes": 300 * MB, "recommendation": "Delete"}, {"size_bytes": 500 * MB, "recommendation": "Review"}]}


def test_notification_only_when_a_scheduled_scan_finds_enough(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda title, text, clickable=True: sent.append((title, text, clickable)) or True)
    assert notify.get_settings()["enabled"] and notify.get_settings()["min_mb"] == 500
    assert not notify.after_report(_report(100))
    assert not notify.after_report(_report(800, source="manual"))
    assert notify.after_report(_report(800))
    title, text, _ = sent[-1]
    assert title == "JunkZero found 800.00 MB to clean"
    assert text.startswith("3 items in your scheduled scan; 300.00 MB of it is marked Delete.")
    assert notify.after_report(error="Folder not found")
    assert sent[-1][0] == "JunkZero's scheduled scan failed" and sent[-1][2] is False
    monkeypatch.setattr(notify.osinfo, "is_windows", lambda: False)
    notify.save_settings(False, 500)
    assert not notify.after_report(_report(5000)) and not notify.after_report(error="x")


def test_a_failing_notification_never_fails_the_scheduled_scan(monkeypatch):
    def boom(*a, **k):
        raise OSError("no notifications here")
    monkeypatch.setattr(notify, "send", boom)
    assert notify.after_report(_report(800)) is False


def test_unseen_report_is_offered_once(monkeypatch):
    monkeypatch.setattr(notify.osinfo, "is_windows", lambda: False)
    report = _report(800)
    unseen = notify.unseen_report(report)
    assert unseen and "items" not in unseen
    notify.mark_seen(report["generated_at"])
    assert notify.unseen_report(report) is None
    notify.save_settings(True, 2000)
    assert notify.get_settings()["seen_at"] == report["generated_at"]
    assert notify.unseen_report(_report(800, at=time.time() + 5)) is None


def test_toast_text_is_escaped():
    xml = notify.toast_xml('Found <5 GB> & "more"', "x")
    assert "&lt;5 GB&gt; &amp;" in xml and 'launch="junkzero:report"' in xml
    assert "launch=" not in notify.toast_xml("a", "b", clickable=False)


def test_notify_and_unseen_report_api(monkeypatch):
    monkeypatch.setattr(notify.osinfo, "is_windows", lambda: False)
    client = api_client()
    assert client.put("/api/notify", json={"enabled": True, "min_mb": 100}).json()["notify"]["min_mb"] == 100
    report = _report(800)
    storage.save_report(report)
    unseen = client.get("/api/reports/unseen").json()["report"]
    assert unseen["delete_bytes"] == 300 * MB
    client.post("/api/reports/seen", json={"generated_at": report["generated_at"]})
    assert client.get("/api/reports/unseen").json()["report"] is None


def test_scheduled_scan_notifies_after_its_report(monkeypatch):
    from app.engine import scheduler
    calls = []
    monkeypatch.setattr(notify, "after_report", lambda report=None, error="": calls.append((report, error)) or True)
    monkeypatch.setattr(scheduler, "get_schedule", lambda: {"paths": [], "include_junk_locations": False})
    monkeypatch.setattr(scheduler, "run_report", lambda *a, **k: {"total_bytes": 1})
    assert scheduler.run_scheduled_report() == 0
    assert calls[-1] == ({"total_bytes": 1}, "")

    def fail(*a, **k):
        raise ValueError("disk gone")
    monkeypatch.setattr(scheduler, "run_report", fail)
    assert scheduler.run_scheduled_report() == 1
    assert calls[-1] == (None, "The scheduled scan failed: disk gone")


# ------------------------------------------------------------- 3. Chat and app caches

def test_app_caches_only_for_closed_apps(tmp_path, monkeypatch):
    monkeypatch.setattr(appcaches.osinfo, "is_macos", lambda: False)
    roaming, local = tmp_path / "Roaming", tmp_path / "Local"
    for sub in ("Cache", "Code Cache"):
        (roaming / "Slack" / sub).mkdir(parents=True)
    (roaming / "Slack" / "storage").mkdir()   # Messages and settings: never offered
    (local / "Steam" / "htmlcache").mkdir(parents=True)
    env = {"APPDATA": str(roaming), "LOCALAPPDATA": str(local)}
    found = {label: path for _id, label, path in appcaches.app_cache_entries(env, running={"explorer.exe"})}
    assert found == {"Slack cache (Cache)": str(roaming / "Slack" / "Cache"),
                     "Slack cache (Code Cache)": str(roaming / "Slack" / "Code Cache"),
                     "Steam cache (htmlcache)": str(local / "Steam" / "htmlcache")}
    running = appcaches.app_cache_entries(env, running={"SLACK.EXE"})
    assert all("Slack" not in label for _id, label, _p in running) and len(running) == 1
    assert appcaches.app_cache_entries(env, running=None) == []


def test_mac_app_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(appcaches.osinfo, "is_macos", lambda: True)
    (tmp_path / "Library" / "Application Support" / "discord" / "GPUCache").mkdir(parents=True)
    (tmp_path / "Library" / "Application Support" / "Code" / "CachedData").mkdir(parents=True)
    env = {"HOME": str(tmp_path)}
    labels = {label for _i, label, _p in appcaches.app_cache_entries(env, running=set())}
    assert labels == {"Discord cache (GPUCache)", "VS Code cache (CachedData)"}
    labels = {label for _i, label, _p in appcaches.app_cache_entries(env, running={"electron"})}
    assert labels == {"Discord cache (GPUCache)"}


def test_running_programs_failure_means_nothing_is_offered(monkeypatch):
    monkeypatch.setattr(appcaches.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    assert appcaches._list_running() is None


# ------------------------------------------------------------- 4. Duplicate folders

def _photos(folder, count=3, size=600 * 1024):
    for i in range(count):
        _write(folder / f"IMG_{i}.jpg", bytes([i]) * size)
    _write(folder / "edits" / "IMG_0-edit.jpg", b"e" * 2000)
    _age_tree(folder)


def test_duplicate_folder_is_listed_and_the_pictures_copy_kept(home, monkeypatch):
    from app.engine import scanner as scanner_mod
    pictures = home / "Pictures"
    monkeypatch.setattr(scanner_mod, "protected_user_folders", lambda: [str(pictures)])
    _photos(pictures / "Trip 2024")
    _photos(home / "Downloads" / "Trip 2024 (copy)")
    # Same names and sizes, different contents: not a copy
    _photos(home / "Downloads" / "Other")
    _write(home / "Downloads" / "Other" / "IMG_1.jpg", b"z" * 600 * 1024)
    _age_tree(home / "Downloads" / "Other")
    _, items = _scan(home, include_duplicates=True)
    folders = {k: v for k, v in items.items() if v.category == CAT_DUPLICATE_FOLDERS}
    assert set(folders) == {"Downloads/Trip 2024 (copy)"}
    item = folders["Downloads/Trip 2024 (copy)"]
    assert item.risk_level == RISK_REVIEW and not item.selected and item.is_directory
    assert "Same 4 files as" in item.reason and str(pictures / "Trip 2024") in item.reason
    assert item.duplicate_paths == [str(pictures / "Trip 2024")]
    # The files inside are not listed again as duplicate files
    assert not any(k.startswith("Downloads/Trip 2024 (copy)/") for k in items)
    score, reasons = smart.SmartScorer().score(item)
    assert any("a folder with the same files is kept" in r for r in reasons)


def test_partly_skipped_folders_are_not_compared(home):
    _photos(home / "a" / "set")
    _photos(home / "b" / "set")
    _write(home / "b" / "set" / ".git" / "HEAD", b"ref")  # Never scanned, so b/set isn't fully seen
    _, items = _scan(home, include_duplicates=True)
    assert not any(i.category == CAT_DUPLICATE_FOLDERS for i in items.values())


def test_only_the_outermost_duplicate_folder_is_listed(home):
    _photos(home / "one" / "album" / "inner")
    _photos(home / "two" / "album" / "inner")
    _write(home / "one" / "notes.txt", b"one")   # one and two differ; their albums don't
    _write(home / "two" / "notes.txt", b"two")
    _, items = _scan(home, include_duplicates=True)
    folders = sorted(k for k, v in items.items() if v.category == CAT_DUPLICATE_FOLDERS)
    assert len(folders) == 1 and folders[0] in ("one/album", "two/album")


def test_duplicate_folder_rechecked_before_delete(home):
    _photos(home / "keep" / "set")
    _photos(home / "copy" / "set")
    a, b = str(home / "keep" / "set"), str(home / "copy" / "set")
    assert dupfolders.same_folder(a, b, same_content)
    _write(home / "keep" / "set" / "IMG_2.jpg", b"changed" * 1000)
    assert not dupfolders.same_folder(a, b, same_content)
    assert not dupfolders.same_folder(a, a, same_content)
    offer([b], category=CAT_DUPLICATE_FOLDERS, is_dir=True, duplicate_paths=[a])
    result = api_client().post("/api/clean", json={"items": [{"path": b}], "permanent": True}).json()
    assert result["deleted_count"] == 0 and os.path.isdir(b)
    assert "no other folder with exactly the same files" in result["errors"][0]["error"]


def test_matching_duplicate_folder_is_deleted(home):
    _photos(home / "keep" / "set")
    _photos(home / "copy" / "set")
    a, b = str(home / "keep" / "set"), str(home / "copy" / "set")
    offer([b], category=CAT_DUPLICATE_FOLDERS, is_dir=True, duplicate_paths=[a])
    result = api_client().post("/api/clean", json={"items": [{"path": b}], "permanent": True}).json()
    assert result["deleted_count"] == 1 and not os.path.exists(b) and os.path.isdir(a)


# ------------------------------------------------------------- 5. Old coding projects

def _node_project(folder, mtime=OLD):
    _write(folder / "package.json", b"{}", mtime)
    _write(folder / "src" / "index.js", b"x", mtime)
    _write(folder / "node_modules" / "left-pad" / "index.js", b"y" * 5000, mtime)
    _age_tree(folder, mtime)


def test_old_project_dependencies_are_listed_for_review(home):
    _node_project(home / "code" / "old-site")
    _node_project(home / "code" / "new-site", mtime=time.time() - 86400)
    _, items = _scan(home, include_old_projects=True)
    olds = {k: v for k, v in items.items() if v.category == CAT_OLD_PROJECTS}
    assert set(olds) == {"code/old-site/node_modules"}
    item = olds["code/old-site/node_modules"]
    assert item.risk_level == RISK_REVIEW and not item.selected and item.size_bytes == 5000
    assert "untouched for" in item.reason and "npm install brings it back" in item.reason
    # Off by default
    _, items = _scan(home)
    assert not any(v.category == CAT_OLD_PROJECTS for v in items.values())


def test_recent_git_activity_keeps_a_project(home):
    _node_project(home / "proj")
    _write(home / "proj" / ".git" / "index", b"i", mtime=time.time())
    assert projects.old_project_folder(str(home / "proj" / "node_modules"), "node_modules") is None


def test_rebuild_hints_need_the_project_file(home):
    _write(home / "py" / "pyproject.toml", b"")
    _write(home / "py" / ".venv" / "pyvenv.cfg", b"home = x")
    _write(home / "rs" / "Cargo.toml", b"")
    (home / "rs" / "target").mkdir()
    (home / "lonely" / "node_modules").mkdir(parents=True)
    (home / "lonely" / "target").mkdir()
    assert "virtual environment" in projects.rebuild_hint(str(home / "py" / ".venv"), ".venv")
    assert projects.rebuild_hint(str(home / "rs" / "target"), "target") == "cargo build brings it back"
    assert projects.rebuild_hint(str(home / "lonely" / "node_modules"), "node_modules") is None
    assert projects.rebuild_hint(str(home / "lonely" / "target"), "target") is None


def test_project_too_big_to_check_is_not_listed(home):
    _node_project(home / "big")
    assert projects.last_activity(str(home / "big"), limit=1) == 0.0


# ------------------------------------------------------------- 6. Installers for installed programs

def test_program_names_compare_without_versions_and_noise():
    assert installed.normalize("7-Zip 23.01 (x64)") == "7zip"
    assert installed.normalize("7-Zip") == "7zip"
    assert installed.normalize("Mozilla Firefox (x64 en-US)") == "mozillafirefox"
    assert installed.normalize("Notepad++ v8.6.2 setup") == "notepad++"
    assert installed.version_tuple("23.01") == installed.version_tuple("23.01.0.0")


def test_already_installed_sentences():
    table = installed.build_table([("7-Zip 23.01 (x64)", "23.01"), ("VLC media player", "3.0.21"),
                                   ("Python 3.13.0 (64-bit)", "3.13.150.0")])
    assert installed.already_installed("7-Zip", "23.01.00.0", table) == "7-Zip 23.01 (x64) is already installed"
    assert installed.already_installed("VLC media player", "3.0.20", table) == \
        "a newer version is already installed: VLC media player 3.0.21"
    assert installed.already_installed("VLC media player", "3.0.22", table) == ""
    assert installed.already_installed("VLC", "3.0.21", table) == ""   # Not the same name: no guess
    assert installed.already_installed("7-Zip", "", table) == ""


def test_installer_for_an_installed_program_scores_higher(tmp_path, monkeypatch):
    table = installed.build_table([("7-Zip 23.01 (x64)", "23.01")])
    monkeypatch.setattr(installed, "installed_table", lambda: table)
    path = make_exe(tmp_path / "Downloads" / "7z2301-x64.exe",
                    resources=b"JR.Inno.Setup" + version_info(ProductName="7-Zip", ProductVersion="23.01"))
    # A month old, so the score isn't already at the cap for Review items
    item = classify_item(str(path), path.name, os.path.getsize(path), time.time() - 30 * 86400, False,
                         ScanOptions(target_path=str(path.parent)))
    assert item.category == CAT_INSTALLERS and item.risk_level == RISK_REVIEW and not item.selected
    assert item.installed_note == "7-Zip 23.01 (x64) is already installed"
    assert item.reason.endswith("; 7-Zip 23.01 (x64) is already installed")
    with_note, reasons = smart.SmartScorer().score(item)
    item.installed_note = ""
    without, _ = smart.SmartScorer().score(item)
    assert "+10: 7-Zip 23.01 (x64) is already installed" in reasons and with_note > without


def test_installed_programs_are_only_read_on_windows(monkeypatch):
    monkeypatch.setattr(installed.osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(installed, "_cache", (0.0, None))
    assert installed.installed_table() == {}


# ------------------------------------------------------------- 7. Free space over time

def test_space_log_keeps_one_point_an_hour_per_disk(tmp_path):
    now = time.time()
    spacelog.record(str(tmp_path), total=1000, free=400, now=now - 7200)
    spacelog.record(str(tmp_path), total=1000, free=300, now=now - 3600)
    spacelog.record(str(tmp_path), total=1000, free=350, now=now - 3000)   # Replaces the last point
    disks = spacelog.history(cleanups=[])["disks"]
    assert len(disks) == 1
    assert [p["free"] for p in disks[0]["points"]] == [400, 350]


def test_space_history_api_lists_cleanups(tmp_path):
    spacelog.record(str(tmp_path), total=1000, free=400)
    storage.record_cleanup({"mode": "recycle_bin", "deleted_count": 1, "freed_bytes": 77}, [str(tmp_path / "x")], "manual")
    data = api_client().get("/api/space/history").json()
    assert data["disks"] and data["cleanups"][0]["freed_bytes"] == 77
    assert data["cleanups"][0]["roots"] == [data["disks"][0]["root"]]


def test_scan_records_free_space(home):
    _write(home / "a.tmp", b"t")
    _scan(home)
    assert spacelog.history(cleanups=[])["disks"]


def test_scan_request_accepts_old_projects_option():
    assert "include_old_projects" in main.SCAN_OPTION_KEYS
    assert cleaner is not None
