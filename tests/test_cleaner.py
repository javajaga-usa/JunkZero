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
