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
    from fastapi.testclient import TestClient
    from app.main import app

    trashed = []
    monkeypatch.setattr("app.engine.cleaner.send2trash.send2trash", lambda p: trashed.append(p))
    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmpdir:
        a = Path(tmpdir) / "a.tmp"
        b = Path(tmpdir) / "b.tmp"
        folder = Path(tmpdir) / "folder"
        a.write_text("a")
        b.write_text("b")
        folder.mkdir()

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
