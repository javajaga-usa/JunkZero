"""Smart scores, learning from what the user deletes and keeps, and program leftovers."""
import os
import time

import pytest

from app import main
from app.config import (
    CAT_BROKEN_DOWNLOADS, CAT_EMPTY_FOLDERS, CAT_INSTALLERS, CAT_LEFTOVERS, CAT_TEMP_JUNK,
    RISK_CAUTION, RISK_REVIEW, RISK_SAFE, ScanOptions,
)
from app.engine import changes, smart
from app.engine.classifier import build_item
from app.engine.leftovers import InstalledPrograms, Leftover, find_leftovers, installed_programs
from app.engine.scanner import FastScanner
from tests.helpers import api_client, offer

client = api_client()
NOW = 1_800_000_000.0
DAY = 86400


def _item(path, category=CAT_TEMP_JUNK, risk=RISK_SAFE, age_days=30, is_dir=False):
    return build_item(path, os.path.basename(path), category, 100, NOW - age_days * DAY, risk, "r", is_dir=is_dir)


def _score(item, learning=None):
    return smart.SmartScorer(learning, now=NOW).score(item)


# ------------------------------------------------------------- Scoring

def test_score_follows_risk_and_explains_itself():
    safe, reasons = _score(_item(r"C:\Temp\a.tmp"))
    review, _ = _score(_item(r"C:\x\setup.zip", CAT_INSTALLERS, RISK_REVIEW))
    caution, _ = _score(_item(r"C:\x\tool.exe", CAT_INSTALLERS, RISK_CAUTION))
    assert safe > review > caution
    assert smart.recommendation(safe) == "Delete"
    assert smart.recommendation(caution) == "Keep"
    assert reasons[0] == "Starts at 80: rated Safe"


def test_recent_files_score_lower_and_old_ones_higher():
    fresh, reasons = _score(_item(r"C:\Temp\a.tmp", age_days=0.1))
    old, old_reasons = _score(_item(r"C:\Temp\a.tmp", age_days=800))
    assert fresh < 75 <= old
    assert any("may still be in use" in r for r in reasons)
    assert any("untouched for 2 years" in r for r in old_reasons)


def test_category_and_location_adjust_the_score():
    broken, _ = _score(_item(r"C:\x\a.crdownload", CAT_BROKEN_DOWNLOADS))
    empty, _ = _score(_item(r"C:\x\gone", CAT_EMPTY_FOLDERS, is_dir=True))
    in_downloads, reasons = _score(_item(r"C:\Users\me\Downloads\setup.zip", CAT_INSTALLERS, RISK_REVIEW))
    elsewhere, _ = _score(_item(r"C:\Users\me\Projects\setup.zip", CAT_INSTALLERS, RISK_REVIEW))
    assert broken > 80 > empty
    assert in_downloads == elsewhere + 5
    assert "+5: sits in Downloads" in reasons


def test_scores_stay_between_1_and_99():
    item = _item(r"C:\Users\me\Downloads\a.crdownload", CAT_BROKEN_DOWNLOADS, age_days=1000)
    assert _score(item, {"deleted": {smart.signature(CAT_BROKEN_DOWNLOADS, "a.crdownload", False): 50}})[0] == 99
    low = _item(r"C:\x\tool.exe", CAT_INSTALLERS, RISK_CAUTION, age_days=0)
    assert _score(low, {"kept_paths": {os.path.normcase(low.path): [5, NOW]}})[0] == 1


def test_scanned_items_carry_a_score(tmp_path):
    (tmp_path / "junk.tmp").write_text("x")
    item, = FastScanner(ScanOptions(target_path=str(tmp_path))).run_scan()
    assert 1 <= item.score <= 99 and item.recommendation and item.score_reasons


# ------------------------------------------------------------- Learning

def test_deleting_similar_items_raises_their_score():
    zips = [{"path": rf"C:\D\{n}.zip", "name": f"{n}.zip", "category": CAT_INSTALLERS} for n in "abc"]
    assert smart.learn_deleted(zips, failed_paths=[r"C:\D\c.zip"]) == 2
    learning = smart.load_learning()
    before, _ = _score(_item(r"C:\x\new.zip", CAT_INSTALLERS, RISK_REVIEW))
    after, reasons = _score(_item(r"C:\x\new.zip", CAT_INSTALLERS, RISK_REVIEW), learning)
    assert after == before + 6
    assert "+6: you deleted 2 .zip files like this before" in reasons
    # A different file type in the same category learns nothing
    assert _score(_item(r"C:\x\new.iso", CAT_INSTALLERS, RISK_REVIEW), learning)[0] == \
        _score(_item(r"C:\x\new.iso", CAT_INSTALLERS, RISK_REVIEW))[0]


def test_items_found_again_count_as_kept_once_a_day():
    item = _item(r"C:\Temp\keep.log")
    assert smart.learn_kept([item], [item.path], now=NOW) == 1
    assert smart.learn_kept([item], [item.path], now=NOW + 3600) == 0  # Same day
    assert smart.learn_kept([item], [item.path], now=NOW + DAY) == 1
    assert smart.learn_kept([_item(r"C:\Temp\other.log")], [item.path], now=NOW + 2 * DAY) == 0
    score, reasons = _score(item, smart.load_learning())
    assert score == _score(item)[0] - 25
    assert "-25: you kept this through 2 earlier scans" in reasons
    assert smart.learning_summary() == {"deleted": 0, "kept": 1}


def test_rescanning_learns_what_was_kept(tmp_path):
    (tmp_path / "keep.tmp").write_text("x")
    options = ScanOptions(target_path=str(tmp_path))
    changes.compare_and_remember(options, FastScanner(options).run_scan())
    changes.compare_and_remember(options, FastScanner(options).run_scan())
    assert smart.learning_summary()["kept"] == 1


def test_clean_endpoint_learns_and_learning_can_be_forgotten(tmp_path):
    junk = tmp_path / "old.bak2"
    junk.write_text("x")
    offer([junk, tmp_path / "missing.bak2"])
    res = client.post("/api/clean", json={"permanent": True, "items": [
        {"path": str(junk), "name": junk.name, "category": CAT_TEMP_JUNK, "size_bytes": 1},
        {"path": str(tmp_path / "missing.bak2"), "name": "missing.bak2", "category": CAT_TEMP_JUNK, "size_bytes": 1},
    ]})
    assert res.json()["deleted_count"] == 1
    assert client.get("/api/learning").json() == {"deleted": 1, "kept": 0}
    assert client.delete("/api/learning").json() == {"deleted": 0, "kept": 0}


def test_corrupt_learning_file_is_ignored():
    smart.storage.write_json("learning.json", ["not", "a", "dict"])
    assert smart.learning_summary() == {"deleted": 0, "kept": 0}


# ------------------------------------------------------------- Program leftovers

def _app_folder(root, name, age_days, now):
    folder = root / name
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "settings.ini").write_text("x" * 50)
    for p in (folder / "sub" / "settings.ini", folder / "sub", folder):
        os.utime(p, (now - age_days * DAY, now - age_days * DAY))
    return folder


INSTALLED = InstalledPrograms(["Spotify", "JetBrains s.r.o.", "Zoom Workplace", "7-Zip 23.01 (x64)",
                               "Microsoft Visual Studio Code", "Adobe Acrobat (64-bit)", "IntelliJ IDEA Community"])


@pytest.mark.parametrize("folder,in_use", [
    ("Spotify", True), ("JetBrains", True), ("Zoom", True), ("7-Zip", True), ("IntelliJIdea2019", True),
    ("Code", True), ("Adobe Acrobat DC", True), ("OldGame Studio", False), ("Studio Tools", True),
    ("ab", True),
])
def test_installed_programs_match_folder_names_loosely(folder, in_use):
    assert INSTALLED.matches(folder) is in_use


def test_only_old_unmatched_unshared_folders_are_leftovers(tmp_path):
    now = time.time()
    _app_folder(tmp_path, "OldGame Studio", 400, now)
    _app_folder(tmp_path, "RecentTool", 10, now)      # Still in use
    _app_folder(tmp_path, "Spotify", 400, now)        # Installed
    _app_folder(tmp_path, "Microsoft Edge", 400, now) # Shared Windows folder
    _app_folder(tmp_path, "npm-cache", 400, now)      # Shared tool folder
    (tmp_path / "loose.txt").write_text("x")
    found = find_leftovers([str(tmp_path)], INSTALLED, now=now)
    assert [(l.name, l.days_unchanged) for l in found] == [("OldGame Studio", 400)]


def test_a_recent_file_deep_inside_keeps_the_folder(tmp_path):
    now = time.time()
    folder = _app_folder(tmp_path, "OldGame Studio", 400, now)
    (folder / "sub" / "fresh.dat").write_text("x")
    os.utime(folder / "sub", (now - 400 * DAY, now - 400 * DAY))
    assert find_leftovers([str(tmp_path)], INSTALLED, now=now) == []


def test_nothing_is_reported_without_an_installed_list(tmp_path):
    _app_folder(tmp_path, "OldGame Studio", 400, time.time())
    assert find_leftovers([str(tmp_path)], InstalledPrograms([])) == []
    if os.name != "nt":
        assert not installed_programs({})


def _leftover(path, days=400):
    return Leftover(str(path), path.name, time.time() - days * DAY, days)


def test_scanner_lists_leftovers_inside_and_outside_the_target(tmp_path):
    target, appdata = tmp_path / "scan", tmp_path / "appdata"
    target.mkdir()
    inside = _app_folder(target, "OldGame", 400, time.time())
    (inside / "sub" / "cache.tmp").write_text("x")
    outside = _app_folder(appdata, "OldTool", 400, time.time())
    excluded = _app_folder(appdata, "Excluded", 400, time.time())
    items = FastScanner(ScanOptions(
        target_path=str(target), include_leftovers=True, exclusions=[str(excluded)],
        leftovers=[_leftover(inside), _leftover(outside), _leftover(excluded)],
    )).run_scan()
    by_path = {i.path: i for i in items}
    assert set(by_path) == {str(inside), str(outside)}  # The .tmp inside is not listed twice
    item = by_path[str(outside)]
    assert (item.category, item.risk_level, item.selected, item.is_directory) == (CAT_LEFTOVERS, RISK_REVIEW, False, True)
    assert item.size_bytes == 50 and "no longer installed" in item.reason


def test_leftovers_are_off_unless_asked_for(tmp_path):
    folder = _app_folder(tmp_path, "OldGame", 400, time.time())
    items = FastScanner(ScanOptions(target_path=str(tmp_path), include_empty_folders=False,
                                    leftovers=[_leftover(folder)])).run_scan()
    assert items == []
