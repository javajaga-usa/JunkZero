"""Unit tests for the Safe Deletion Cleaner."""
import os
import tempfile
from pathlib import Path
from app.engine.cleaner import delete_items


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
