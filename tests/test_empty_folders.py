"""Tests for empty folder detection and the empty-folder deletion guard."""
import os
import tempfile
from pathlib import Path

from app.config import CAT_EMPTY_FOLDERS, RISK_SAFE, ScanOptions
from app.engine.cleaner import delete_items
from app.engine.scanner import FastScanner


def _scan(path: str, **kwargs):
    scanner = FastScanner(ScanOptions(target_path=path, **kwargs))
    items = scanner.run_scan()
    return scanner, [i for i in items if i.category == CAT_EMPTY_FOLDERS]


def test_detects_empty_folder():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "empty").mkdir()
        (base / "full").mkdir()
        (base / "full" / "notes.txt").write_text("keep me")

        _, empty = _scan(tmpdir)

        assert [i.name for i in empty] == ["empty"]
        item = empty[0]
        assert item.is_directory is True
        assert item.size_bytes == 0
        assert item.risk_level == RISK_SAFE
        assert item.reason == "Empty folder (contains no files)"


def test_reports_only_topmost_folder_of_empty_tree():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "outer" / "a" / "deep").mkdir(parents=True)
        (base / "outer" / "b").mkdir()

        _, empty = _scan(tmpdir)

        assert [i.name for i in empty] == ["outer"]
        assert "3 nested empty subfolders" in empty[0].reason


def test_folder_with_file_deep_inside_is_not_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "project" / "src" / "pkg").mkdir(parents=True)
        (base / "project" / "src" / "pkg" / "main.py").write_text("print('hi')")
        (base / "project" / "docs").mkdir()

        _, empty = _scan(tmpdir)

        # Only the empty sibling branch is reported, never its non-empty ancestors
        assert [Path(i.path).relative_to(base).as_posix() for i in empty] == ["project/docs"]


def test_garbage_file_still_makes_folder_non_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "logs").mkdir()
        (base / "logs" / "old.log").write_text("x")

        scanner, empty = _scan(tmpdir)

        assert empty == []
        assert any(i.name == "old.log" for i in scanner.garbage_items)


def test_build_directory_is_not_reported_as_empty_parent_content():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "app" / "node_modules").mkdir(parents=True)

        _, empty = _scan(tmpdir)

        assert all(i.name != "app" for i in empty)


def test_scan_root_is_never_reported():
    with tempfile.TemporaryDirectory() as tmpdir:
        scanner, empty = _scan(tmpdir)
        assert empty == []
        assert scanner.stats.is_completed is True


def test_option_disables_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "empty").mkdir()
        _, empty = _scan(tmpdir, include_empty_folders=False)
        assert empty == []


def test_empty_folders_are_counted_in_stats_and_callback():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "one").mkdir()
        (Path(tmpdir) / "two").mkdir()

        scanner = FastScanner(ScanOptions(target_path=tmpdir))
        seen = []
        scanner.set_callback(lambda item, stats: seen.append(item.category))
        scanner.run_scan()

        assert scanner.stats.category_counts.get(CAT_EMPTY_FOLDERS) == 2
        assert seen.count(CAT_EMPTY_FOLDERS) == 2


def test_delete_empty_folder():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "outer" / "inner"
        target.mkdir(parents=True)
        _, empty = _scan(tmpdir)

        result = delete_items([i.model_dump() for i in empty], permanent=True)

        assert result.deleted_count == 1
        assert not (Path(tmpdir) / "outer").exists()


def test_delete_skips_folder_that_gained_a_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "outer" / "inner").mkdir(parents=True)
        _, empty = _scan(tmpdir)

        # A file appears after the scan finished
        (Path(tmpdir) / "outer" / "inner" / "new.txt").write_text("new data")

        result = delete_items([i.model_dump() for i in empty], permanent=True)

        assert result.deleted_count == 0
        assert result.failed_count == 1
        assert "no longer empty" in result.errors[0]["error"]
        assert (Path(tmpdir) / "outer" / "inner" / "new.txt").exists()
