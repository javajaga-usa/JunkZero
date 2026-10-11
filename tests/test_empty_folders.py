"""Tests for empty folder detection and the empty-folder deletion guard."""
import os
import tempfile
from pathlib import Path

import pytest

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


# --- Hidden items: a folder holding anything hidden is never empty ---

def _hide_windows(path: Path) -> None:
    """Set the Windows hidden attribute (or hidden + system) on a file or folder."""
    import ctypes
    attrs = 0x2 | (0x4 if path.suffix == ".sys" else 0)
    assert ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs)


def test_is_hidden_reads_names_and_attributes():
    from types import SimpleNamespace
    from app.engine.osinfo import is_hidden

    plain = SimpleNamespace()
    assert is_hidden(".config", plain)
    assert is_hidden(".DS_Store")
    assert not is_hidden("Documents", plain)
    assert not is_hidden("Documents")
    assert is_hidden("desktop.ini", SimpleNamespace(st_file_attributes=0x2))  # Windows hidden
    assert is_hidden("pagefile", SimpleNamespace(st_file_attributes=0x4))  # Windows system
    assert not is_hidden("notes.txt", SimpleNamespace(st_file_attributes=0x20))  # archive only
    if hasattr(__import__("stat"), "UF_HIDDEN"):
        import stat
        assert is_hidden("Icon", SimpleNamespace(st_flags=stat.UF_HIDDEN))  # Finder hidden flag


def test_dot_file_makes_folder_tree_not_empty(tmp_path):
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    (tmp_path / "outer" / "inner" / ".keep").write_text("")
    (tmp_path / "really-empty").mkdir()

    _, empty = _scan(str(tmp_path))

    assert [i.name for i in empty] == ["really-empty"]


def test_empty_hidden_subfolder_makes_folder_not_empty(tmp_path):
    (tmp_path / "app" / ".cache").mkdir(parents=True)

    _, empty = _scan(str(tmp_path))

    # Neither the parent nor the hidden folder itself is offered as empty
    assert empty == []


def test_hidden_folder_is_still_scanned_for_junk(tmp_path):
    (tmp_path / ".tool").mkdir()
    (tmp_path / ".tool" / "crash.dmp").write_bytes(b"x" * 10)

    scanner = FastScanner(ScanOptions(target_path=str(tmp_path)))
    items = scanner.run_scan()

    assert any(i.name == "crash.dmp" for i in items)


@pytest.mark.skipif(os.name != "nt", reason="Windows file attributes")
def test_windows_hidden_file_makes_folder_not_empty(tmp_path):
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    hidden = tmp_path / "outer" / "inner" / "thumbs-cache"
    hidden.write_text("")
    _hide_windows(hidden)

    _, empty = _scan(str(tmp_path))

    assert empty == []


@pytest.mark.skipif(os.name != "nt", reason="Windows file attributes")
def test_windows_hidden_and_system_folder_makes_folder_not_empty(tmp_path):
    (tmp_path / "outer" / "store.sys").mkdir(parents=True)
    _hide_windows(tmp_path / "outer" / "store.sys")

    _, empty = _scan(str(tmp_path))

    assert empty == []


@pytest.mark.skipif(not hasattr(os, "chflags") or not hasattr(__import__("stat"), "UF_HIDDEN"),
                    reason="Finder hidden flag needs macOS/BSD")
def test_finder_hidden_folder_makes_folder_not_empty(tmp_path):
    import stat
    (tmp_path / "outer" / "Secret").mkdir(parents=True)
    try:
        os.chflags(tmp_path / "outer" / "Secret", stat.UF_HIDDEN)
    except OSError:
        pytest.skip("file system does not support the hidden flag")

    _, empty = _scan(str(tmp_path))

    assert empty == []


def test_delete_skips_folder_that_gained_a_hidden_file(tmp_path):
    (tmp_path / "outer" / "inner").mkdir(parents=True)
    _, empty = _scan(str(tmp_path))
    assert [i.name for i in empty] == ["outer"]

    (tmp_path / "outer" / "inner" / ".settings").write_text("")

    result = delete_items([i.model_dump() for i in empty], permanent=True)

    assert result.deleted_count == 0
    assert "no longer empty" in result.errors[0]["error"]
    assert (tmp_path / "outer" / "inner" / ".settings").exists()


def test_delete_skips_folder_that_gained_an_empty_hidden_folder(tmp_path):
    (tmp_path / "outer").mkdir()
    _, empty = _scan(str(tmp_path))

    (tmp_path / "outer" / ".git-like").mkdir()

    result = delete_items([i.model_dump() for i in empty], permanent=True)

    assert result.deleted_count == 0
    assert (tmp_path / "outer" / ".git-like").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows file attributes")
def test_delete_skips_folder_that_gained_a_windows_hidden_file(tmp_path):
    (tmp_path / "outer").mkdir()
    _, empty = _scan(str(tmp_path))

    hidden = tmp_path / "outer" / "desktop.ini"
    hidden.write_text("")
    _hide_windows(hidden)

    result = delete_items([i.model_dump() for i in empty], permanent=True)

    assert result.deleted_count == 0
    assert hidden.exists()
