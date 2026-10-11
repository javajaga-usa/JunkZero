"""Unit tests for the Safe Deletion Cleaner."""
import os
import tempfile
from pathlib import Path
from app.engine.cleaner import delete_items
from tests.helpers import api_client, offer


def test_safe_cleaner_permanent_delete():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "junk.tmp"
        test_file.write_text("temporary data")
        assert test_file.exists()

        items = [{
            "path": str(test_file),
            "size_bytes": 14,
            "is_directory": False
        }]

        result = delete_items(items, permanent=True)
        assert result.deleted_count == 1
        assert result.failed_count == 0
        assert not test_file.exists()


def test_safe_cleaner_blocks_system_path():
    system_item = [{
        "path": "C:/Windows/System32/notepad.exe",
        "size_bytes": 200000,
        "is_directory": False
    }]

    result = delete_items(system_item, permanent=False)
    assert result.deleted_count == 0
    assert result.failed_count == 1
    assert len(result.errors) > 0
    assert "protected system path" in result.errors[0]["error"]


def test_default_deletion_uses_recycle_bin(tmp_path, monkeypatch):
    file = tmp_path / 'junk.tmp'
    file.write_text('junk')
    recycled = []
    monkeypatch.setattr('app.engine.cleaner.send2trash.send2trash', recycled.append)
    result = delete_items([{'path': str(file)}])
    assert recycled == [str(file)]
    assert result.mode == 'recycle_bin'
    assert result.deleted_count == 1
    assert file.exists()


def test_missing_and_invalid_items_are_reported(tmp_path):
    result = delete_items([{'path': str(tmp_path / 'missing')}, {'path': ''},
                           {'path': None}, {'path': str(tmp_path), 'size_bytes': -1}])
    assert result.failed_count == result.total_requested == 4
    assert result.deleted_count == 0


def test_root_deletion_is_blocked(tmp_path):
    result = delete_items([{'path': Path(tmp_path.anchor).as_posix()}], permanent=True)
    assert result.failed_count == 1
    assert 'root drive' in result.errors[0]['error']


def test_symlink_deletion_is_blocked(tmp_path):
    import pytest
    folder = tmp_path / 'original'
    folder.mkdir()
    (folder / 'keep.txt').write_text('keep')
    link = tmp_path / 'link'
    try:
        link.symlink_to(folder, target_is_directory=True)
    except OSError:
        pytest.skip('Creating symlinks requires privileges on this host')
    result = delete_items([{'path': str(link)}], permanent=True)
    assert result.failed_count == 1
    assert (folder / 'keep.txt').exists()


def test_cleaner_defaults_to_recycle_bin(monkeypatch):
    trashed = []
    monkeypatch.setattr("app.engine.cleaner.send2trash.send2trash", lambda p: trashed.append(p))

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "junk.tmp"
        test_file.write_text("temporary data")

        result = delete_items([{"path": str(test_file), "size_bytes": 14, "is_directory": False}])

        assert result.mode == "recycle_bin"
        assert result.deleted_count == 1
        assert trashed == [os.path.abspath(str(test_file))]
        # File is handed to the Recycle Bin, never removed directly
        assert test_file.exists()


def test_api_deletes_go_to_recycle_bin_unless_permanent_requested(monkeypatch):
    from app.main import app

    trashed = []
    monkeypatch.setattr("app.engine.cleaner.send2trash.send2trash", lambda p: trashed.append(p))
    client = api_client()

    with tempfile.TemporaryDirectory() as tmpdir:
        a = Path(tmpdir) / "a.tmp"
        b = Path(tmpdir) / "b.tmp"
        folder = Path(tmpdir) / "folder"
        a.write_text("a")
        b.write_text("b")
        folder.mkdir()

        offer([a, b])
        res = client.post("/api/clean", json={"items": [{"path": str(a)}]})
        assert res.json()["mode"] == "recycle_bin"
        assert a.exists()

        res = client.post("/api/filesystem/delete-folder", json={"path": str(folder)})
        assert res.json()["mode"] == "recycle_bin"
        assert folder.exists()
        assert len(trashed) == 2

        res = client.post("/api/clean", json={"items": [{"path": str(b)}], "permanent": True})
        assert res.json()["mode"] == "permanent"
        assert not b.exists()
