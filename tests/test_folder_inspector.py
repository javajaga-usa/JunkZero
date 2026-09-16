"""Unit tests for the folder hierarchy inspector and parent level deletion."""
import os
import tempfile
from pathlib import Path
from app.engine.inspector import inspect_path_hierarchy, build_breadcrumbs
from app.engine.cleaner import delete_items


def test_inspect_path_hierarchy_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        subfolder = base / "project" / "build"
        subfolder.mkdir(parents=True)

        target_file = subfolder / "setup.exe"
        target_file.write_text("dummy exe")

        sibling_file = subfolder / "notes.txt"
        sibling_file.write_text("some notes")

        view = inspect_path_hierarchy(str(target_file))

        # Current inspected path should be the parent folder (subfolder)
        assert os.path.samefile(view.current_path, str(subfolder))
        assert view.target_file_path is not None
        assert os.path.samefile(view.target_file_path, str(target_file))

        # Parent path should be "project"
        assert view.parent_path is not None
        assert os.path.samefile(view.parent_path, str(base / "project"))

        # Check entries: setup.exe and notes.txt
        entry_names = {e.name for e in view.entries}
        assert "setup.exe" in entry_names
        assert "notes.txt" in entry_names

        # Check target flag
        target_entries = [e for e in view.entries if e.is_target_file]
        assert len(target_entries) == 1
        assert target_entries[0].name == "setup.exe"


def test_breadcrumbs_root_protection():
    crumbs = build_breadcrumbs("C:/Users/Test/Downloads")
    assert len(crumbs) >= 3
    # Drive root should not be deletable
    assert crumbs[0].is_deletable is False


def test_delete_folder_level_permanently():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        folder_to_delete = base / "obsolete_build"
        folder_to_delete.mkdir()
        (folder_to_delete / "file1.tmp").write_text("junk")
        (folder_to_delete / "file2.tmp").write_text("junk2")

        assert folder_to_delete.exists()

        result = delete_items(
            [{"path": str(folder_to_delete), "is_directory": True, "size_bytes": 0}],
            permanent=True
        )

        assert result.deleted_count == 1
        assert not folder_to_delete.exists()
