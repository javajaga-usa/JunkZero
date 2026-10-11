"""Audit fixes: nothing uncertain is listed as sure, and nothing uncertain is pre-ticked."""
import gzip
import os
import time
from pathlib import Path

import pytest

from app.config import (
    CAT_INSTALLERS, CAT_JAVA_BUILDS, CAT_OLD_DOWNLOADS, CAT_TEMP_JUNK, RISK_CAUTION, RISK_REVIEW, RISK_SAFE,
    ScanOptions,
)
from app.engine import scanner as scanner_mod
from app.engine import smart
from app.engine.ai_advisor import analyze_item
from app.engine.archives import inspect_archive
from app.engine.classifier import _is_mac_protected, build_item, classify_item
from app.engine.leftovers import InstalledPrograms, find_leftovers, holds_personal_files, newest_change
from app.engine.safeguards import is_personal_file
from app.engine.scanner import FastScanner

OLD = time.time() - 400 * 86400


def _scan(root, **kw):
    kw.setdefault("cloud_folders", [])
    kw.setdefault("leftovers", [])
    return FastScanner(ScanOptions(target_path=str(root), **kw)).run_scan()


def _touch(path, data=b"x", mtime=OLD):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


# 1. Junctions are never followed or counted

def test_junctions_are_not_walked_or_sized(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_mod, "_is_junction", lambda entry: entry.name == "link")
    _touch(tmp_path / "link" / "temp" / "a.tmp", b"x" * 100)
    _touch(tmp_path / "proj" / "temp" / "b.tmp")
    names = {i.name for i in _scan(tmp_path)}
    assert "b.tmp" in names and "a.tmp" not in names
    assert FastScanner(ScanOptions(target_path=str(tmp_path)))._calc_dir_size(str(tmp_path)) == 1


# 2+3. Smart Score never pushes a Review or Caution item into Delete

@pytest.mark.parametrize("risk", [RISK_REVIEW, RISK_CAUTION])
def test_only_safe_items_can_be_marked_delete(risk):
    item = build_item(r"C:\Users\a\Downloads\x.zip", "x.zip", CAT_INSTALLERS, 1, OLD, risk, "r")
    item.archive_summary, item.archive_clean = "setup.exe inside", True
    learning = {"deleted": {smart.signature(CAT_INSTALLERS, "x.zip", False): 9}}
    smart.SmartScorer(learning).apply(item)
    assert item.score < smart.SCORE_DELETE and item.recommendation != "Delete" and not item.selected


def test_learning_does_not_tick_review_build_folders(tmp_path):
    nm = tmp_path / ".vscode" / "extensions" / "x" / "node_modules"
    _touch(nm / "index.js")
    os.utime(nm, (OLD, OLD))
    learning = {"deleted": {smart.signature(CAT_JAVA_BUILDS, "node_modules", True): 5}}
    item, = [i for i in _scan(tmp_path, learning=learning, include_empty_folders=False) if i.name == "node_modules"]
    assert item.recommendation != "Delete" and not item.selected


def test_compressed_disk_image_is_not_a_setup_archive(tmp_path):
    path = tmp_path / "Downloads" / "pi-backup-2024.img.gz"
    path.parent.mkdir()
    with gzip.open(path, "wb") as f:
        f.write(b"\0" * 1000)
    os.utime(path, (OLD, OLD))
    assert not inspect_archive(str(path)).is_setup
    items = _scan(tmp_path, include_old_downloads=True, downloads_dirs=[str(path.parent)])
    assert all(i.category != CAT_INSTALLERS and i.recommendation != "Delete" and not i.selected for i in items)


# 4. Setup scripts match whole installer words only

@pytest.mark.parametrize("name, flagged", [
    ("research.sh", False), ("build.sh", False), ("update.bat", False), ("backup-pack.ps1", False),
    ("release.cmd", False), ("setup.bat", True), ("install_deps.sh", True), ("Installer-x64.ps1", True),
])
def test_setup_scripts_need_a_whole_installer_word(name, flagged):
    item = classify_item(f"C:/Users/a/scripts/{name}", name, 10, OLD, False, ScanOptions(target_path="."))
    assert (item is not None) is flagged


# 5. Pre-ticked only when marked Delete; protected folders never offered as empty

def test_safe_item_below_delete_is_not_preselected():
    item = build_item(r"C:\Temp\a.tmp", "a.tmp", CAT_TEMP_JUNK, 1, time.time() - 3 * 86400, RISK_SAFE, "r")
    assert item.selected
    smart.SmartScorer({}).apply(item)
    assert item.score < smart.SCORE_DELETE and not item.selected


def test_select_all_safe_respects_the_server():
    js = (Path(__file__).resolve().parent.parent / "app" / "ui" / "app.js").read_text(encoding="utf-8")
    start = js.index("el.btnSelectAllSafe.addEventListener")
    handler = js[start:js.index("});", start)]
    assert "i.selected" in handler and "'delete'" in handler


def test_empty_personal_folders_are_not_offered(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    (home / "Music").mkdir()
    (home / "Projects" / "old").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("USERPROFILE", raising=False)
    names = {i.name for i in _scan(home)}
    assert "old" in names or "Projects" in names
    assert not {"Desktop", "Music"} & names


# 6. Never scan inside version-control or backup folders

@pytest.mark.parametrize("folder", [".git", ".svn", "FileHistory"])
def test_scan_root_inside_vcs_or_backup_is_skipped(tmp_path, folder):
    inner = tmp_path / folder / "objects"
    _touch(inner / "temp" / "gc.tmp")
    (inner / "empty").mkdir()
    assert _scan(inner) == []


# 7. A leftover check that hits its limit counts as recent

def test_unfinished_newest_change_is_not_called_old(tmp_path):
    app = tmp_path / "ActiveTool"
    for i in range(30):
        _touch(app / f"f{i:02}.bin")
    os.utime(app, (OLD, OLD))
    assert newest_change(str(app), limit=10) == 0
    assert newest_change(str(app)) > 0
    assert find_leftovers([str(tmp_path)], InstalledPrograms(["Something Else"])) != []
    import app.engine.leftovers as lo
    orig = lo.newest_change
    lo.newest_change = lambda p: orig(p, limit=10)
    try:
        assert find_leftovers([str(tmp_path)], InstalledPrograms(["Something Else"])) == []
    finally:
        lo.newest_change = orig


# 8. Smaller rules

def test_wallets_count_as_personal(tmp_path):
    assert is_personal_file("wallet.dat") and is_personal_file("main.wallet")
    (tmp_path / "a" / "wallets").mkdir(parents=True)
    assert holds_personal_files(str(tmp_path / "a"))
    assert not holds_personal_files(str(tmp_path / "a" / "wallets"))


def test_inspector_is_no_surer_than_the_scanner(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (tmp_path / "__pycache__").mkdir()
    assert analyze_item(str(build)).safety_verdict == "Review Carefully"
    assert analyze_item(str(tmp_path / "__pycache__")).safety_verdict == "Safe to Delete"
    # Paths need not exist (tmp_path itself sits in a temp folder)
    for path in ("/home/u/disk.img", "/home/u/disc.iso", "/home/u/Documents/notes.tmp", "/home/u/crash.dmp"):
        assert analyze_item(path).safety_verdict == "Review Carefully", path
    assert analyze_item("/home/u/AppData/Local/Temp/x.tmp").safety_verdict == "Safe to Delete"


@pytest.mark.parametrize("name", ["app.war", "app.ear"])
def test_war_and_ear_have_a_reason(name):
    item = classify_item(f"C:/Users/a/{name}", name, 10, OLD, False, ScanOptions(target_path="."))
    assert item.reason and item.risk_level == RISK_REVIEW


def test_library_on_another_disk_is_protected():
    assert _is_mac_protected("volumes/oldmac/users/bob/library/preferences/x.plist".split("/"))
    assert not _is_mac_protected("volumes/oldmac/users/bob/library/caches/x".split("/"))


@pytest.mark.parametrize("path, risk", [
    ("C:/Users/a/tools/Main.class", RISK_REVIEW),
    ("C:/proj/target/classes/Main.class", RISK_SAFE),
    ("C:/Users/a/app/main.pyc", RISK_REVIEW),
    ("C:/proj/__pycache__/main.cpython-313.pyc", RISK_SAFE),
])
def test_compiled_code_is_safe_only_in_build_folders(path, risk):
    item = classify_item(path, path.rsplit("/", 1)[-1], 10, OLD, False, ScanOptions(target_path="."))
    assert item.risk_level == risk and item.selected is (risk == RISK_SAFE)
