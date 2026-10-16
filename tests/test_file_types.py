"""File type search: pick extensions, and matching files are listed for the deletion list."""
import os
import threading
import time

import pytest

from app import main
from app.config import CAT_FILE_TYPES, RISK_REVIEW, ScanOptions
from app.engine import changes, storage
from app.engine.filetypes import FileTypeError, FileTypeMatcher, normalize_types, presets
from app.engine.scanner import FastScanner
from tests.helpers import api_client

client = api_client()
OLD = time.time() - 400 * 86400


def _scan(path, **kwargs):
    kwargs.setdefault("include_file_types", True)
    return FastScanner(ScanOptions(target_path=str(path), cloud_folders=[], **kwargs)).run_scan()


def _type_items(items):
    return sorted(os.path.basename(i.path) for i in items if i.category == CAT_FILE_TYPES)


def _old(path, data="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    os.utime(path, (OLD, OLD))
    return path


# ------------------------------------------------------------------ Normalizing input

def test_types_are_normalized_from_any_common_spelling():
    assert normalize_types(["MP4", ".mov", "*.mkv", " tar.gz ", "mp4", "", "  "]) == ["mp4", "mov", "mkv", "tar.gz"]


@pytest.mark.parametrize("bad", ["*", "*.*", "../x", "C:\\Videos", "a b", "mp4/", ".", "x" * 17, "a.b.c.d"])
def test_paths_wildcards_and_junk_are_rejected(bad):
    if bad == ".":
        assert normalize_types([bad]) == []  # nothing left after the dot: ignored
        return
    with pytest.raises(FileTypeError):
        normalize_types([bad])


@pytest.mark.parametrize("ext", ["tib", ".mrimg", "vhdx", "vmdk"])
def test_backup_images_and_vm_disks_are_refused(ext):
    with pytest.raises(FileTypeError, match="never lists"):
        normalize_types([ext])


def test_too_many_types_are_refused():
    with pytest.raises(FileTypeError):
        normalize_types([f"t{i}" for i in range(101)])


def test_presets_are_valid_types():
    for preset in presets():
        assert normalize_types(preset["types"]) == preset["types"]


def test_longest_type_wins_and_bare_dotfiles_do_not_match():
    matcher = FileTypeMatcher(["gz", "tar.gz", "log"])
    assert matcher.match("backup.TAR.GZ") == "tar.gz"
    assert matcher.match("x.gz") == "gz"
    assert matcher.match(".log") is None
    assert matcher.match("notes.txt") is None


# ------------------------------------------------------------------ Scanning

def test_matching_files_are_listed_for_review_and_not_preselected(tmp_path):
    _old(tmp_path / "a" / "clip.MP4")
    _old(tmp_path / "b" / "movie.mkv")
    _old(tmp_path / "keep.txt")
    items = [i for i in _scan(tmp_path, file_types=["mp4", "mkv"]) if i.category == CAT_FILE_TYPES]
    assert _type_items(items) == ["clip.MP4", "movie.mkv"]
    for item in items:
        assert item.risk_level == RISK_REVIEW
        assert item.selected is False
        assert "File type you searched for" in item.reason


def test_search_is_off_unless_turned_on(tmp_path):
    _old(tmp_path / "clip.mp4")
    assert _type_items(_scan(tmp_path, include_file_types=False, file_types=["mp4"])) == []


def test_size_and_age_filters_apply(tmp_path):
    _old(tmp_path / "small.mp4", "x")
    _old(tmp_path / "big.mp4", "x" * 4096)
    recent = tmp_path / "recent.mp4"
    recent.write_text("x" * 4096)
    names = _type_items(_scan(tmp_path, file_types=["mp4"], min_file_size_bytes=1024, file_type_min_days=30))
    assert names == ["big.mp4"]


def test_exclusions_and_version_control_folders_are_respected(tmp_path):
    _old(tmp_path / "skip" / "a.mp4")
    _old(tmp_path / ".git" / "b.mp4")
    _old(tmp_path / "ok" / "c.mp4")
    names = _type_items(_scan(tmp_path, file_types=["mp4"], exclusions=[str(tmp_path / "skip")]))
    assert names == ["c.mp4"]


def test_built_in_categories_keep_their_files(tmp_path):
    _old(tmp_path / "file.crdownload")
    items = _scan(tmp_path, file_types=["crdownload"])
    assert _type_items(items) == []
    assert [i.name for i in items] == ["file.crdownload"]


def test_personal_files_are_flagged_by_safeguards(tmp_path):
    _old(tmp_path / "report.docx")
    item = next(i for i in _scan(tmp_path, file_types=["docx"]) if i.category == CAT_FILE_TYPES)
    assert item.personal is True
    assert item.selected is False


# ------------------------------------------------------------------ API

def _run_api_scan(tmp_path, monkeypatch, **payload):
    done = threading.Event()
    original = main.FastScanner.run_scan

    def run(self):
        try:
            return original(self)
        finally:
            done.set()

    monkeypatch.setattr(main.FastScanner, "run_scan", run)
    # Kept open until the scan thread has posted its completion message
    with api_client() as session:
        res = session.post("/api/scan/start", json={"target_path": str(tmp_path), **payload})
        assert res.status_code == 200, res.text
        assert done.wait(15)
        time.sleep(0.2)
    return main.current_scanner


def test_type_only_search_turns_every_other_category_off(tmp_path, monkeypatch):
    _old(tmp_path / "clip.mp4")
    _old(tmp_path / "broken.crdownload")
    (tmp_path / "empty").mkdir()
    scanner = _run_api_scan(tmp_path, monkeypatch, type_search_only=True, file_types=[".mp4"])
    assert [i.name for i in scanner.garbage_items] == ["clip.mp4"]
    opts = scanner.options
    assert opts.include_file_types and not (opts.include_broken_downloads or opts.include_empty_folders
                                             or opts.include_custom_rules or opts.include_installers)


def test_type_only_search_does_not_replace_the_last_cleanup_scan(tmp_path, monkeypatch):
    _old(tmp_path / "clip.mp4")
    calls = []
    monkeypatch.setattr(changes, "compare_and_remember", lambda *a: calls.append(a) or {})
    _run_api_scan(tmp_path, monkeypatch, type_search_only=True, file_types=["mp4"])
    time.sleep(0.2)
    assert calls == []


def test_found_files_can_be_deleted_from_the_scan_results(tmp_path, monkeypatch):
    clip = _old(tmp_path / "clip.mp4")
    _run_api_scan(tmp_path, monkeypatch, type_search_only=True, file_types=["mp4"])
    preview = client.post("/api/delete-preview", json={"paths": [str(clip)], "permanent": True})
    assert preview.status_code == 200
    res = client.post("/api/clean", json={"items": [{"path": str(clip)}], "permanent": True,
                                          "confirm_text": "DELETE"})
    assert res.status_code == 200, res.text
    assert res.json()["deleted_count"] == 1
    assert not clip.exists()


@pytest.mark.parametrize("payload, status", [
    ({"include_file_types": True, "file_types": []}, 400),
    ({"type_search_only": True, "file_types": []}, 400),
    ({"include_file_types": True, "file_types": ["*"]}, 400),
    ({"include_file_types": True, "file_types": ["tib"]}, 400),
    ({"include_file_types": True, "file_types": ["mp4"], "file_type_min_days": -1}, 422),
])
def test_bad_searches_are_refused(tmp_path, payload, status):
    assert client.post("/api/scan/start", json={"target_path": str(tmp_path), **payload}).status_code == status


def test_presets_endpoint_and_preferences_remember_types():
    data = client.get("/api/file-types/presets").json()
    assert {p["id"] for p in data["presets"]} >= {"videos", "images", "documents", "archives"}
    res = client.put("/api/preferences", json={
        "scan_options": {"include_file_types": True}, "file_types": [".MP4", "mov"], "file_type_min_days": 30})
    assert res.status_code == 200
    prefs = storage.get_preferences()
    assert prefs["file_types"] == ["mp4", "mov"]
    assert prefs["file_type_min_days"] == 30
    assert prefs["scan_options"] == {"include_file_types": True}
    assert client.put("/api/preferences", json={"file_types": ["../x"]}).status_code == 400
