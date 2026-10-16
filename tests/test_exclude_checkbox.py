"""Excluded items (the Exclusions list, or the Exclude box in a result row) are never deleted."""
import pytest

from app.engine import cleaner, storage
from app.engine.exclusions import ExclusionMatcher
from tests.helpers import api_client, offer

client = api_client()


def test_excluded_file_is_refused(tmp_path):
    f = tmp_path / "keep.tmp"
    f.write_text("x")
    result = cleaner.delete_items([{"path": str(f), "size_bytes": 1}], permanent=True, exclusions=[str(f)])
    assert result.deleted_count == 0
    assert "Excluded" in result.errors[0]["error"] and "remove it from Exclusions" in result.errors[0]["error"]
    assert f.exists()


def test_file_inside_an_excluded_folder_or_matching_a_pattern_is_refused(tmp_path):
    (tmp_path / "keep").mkdir()
    inside = tmp_path / "keep" / "a.tmp"
    iso = tmp_path / "win.ISO"
    inside.write_text("x")
    iso.write_text("x")
    result = cleaner.delete_items([{"path": str(inside)}, {"path": str(iso)}], permanent=True,
                                  exclusions=[str(tmp_path / "keep"), "*.iso"])
    assert result.deleted_count == 0 and inside.exists() and iso.exists()


def test_folder_holding_an_excluded_item_is_kept_whole(tmp_path):
    folder = tmp_path / "build"
    (folder / "sub").mkdir(parents=True)
    keep = folder / "sub" / "notes.txt"
    keep.write_text("keep")
    (folder / "junk.o").write_text("x")
    result = cleaner.delete_items([{"path": str(folder), "is_directory": True}], permanent=True,
                                  exclusions=[str(keep)])
    assert result.deleted_count == 0
    assert "holds an excluded item" in result.errors[0]["error"]
    assert keep.exists()


def test_folder_holding_a_file_matching_an_excluded_pattern_is_kept(tmp_path):
    folder = tmp_path / "cache"
    folder.mkdir()
    (folder / "disk.iso").write_text("x")
    result = cleaner.delete_items([{"path": str(folder), "is_directory": True}], permanent=True, exclusions=["*.iso"])
    assert result.deleted_count == 0 and folder.exists()


def test_an_excluded_path_that_no_longer_exists_does_not_block_its_folder(tmp_path):
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / "x.tmp").write_text("x")
    result = cleaner.delete_items([{"path": str(folder), "is_directory": True}], permanent=True,
                                  exclusions=[str(folder / "gone.txt")])
    assert result.deleted_count == 1 and not folder.exists()


def test_other_items_in_the_same_delete_still_go(tmp_path):
    keep, junk = tmp_path / "keep.tmp", tmp_path / "junk.tmp"
    keep.write_text("x")
    junk.write_text("x")
    result = cleaner.delete_items([{"path": str(keep)}, {"path": str(junk)}], permanent=True, exclusions=[str(keep)])
    assert (result.deleted_count, keep.exists(), junk.exists()) == (1, True, False)


def test_saved_exclusions_apply_by_default(tmp_path):
    f = tmp_path / "keep.tmp"
    f.write_text("x")
    storage.set_exclusions([str(f)])
    assert cleaner.delete_items([{"path": str(f)}], permanent=True).deleted_count == 0
    assert f.exists()


def test_scan_results_delete_refuses_an_item_excluded_after_the_scan(tmp_path):
    f = tmp_path / "old.tmp"
    f.write_text("x")
    offer([f])
    assert client.post("/api/exclusions/add", json={"rule": str(f)}).status_code == 200
    res = client.post("/api/clean", json={"items": [{"path": str(f)}], "permanent": True}).json()
    assert res["deleted_count"] == 0 and "Excluded" in res["errors"][0]["error"]
    assert f.exists()
    # Removing it from Exclusions makes it deletable again
    client.put("/api/exclusions", json={"rules": []})
    res = client.post("/api/clean", json={"items": [{"path": str(f)}], "permanent": True}).json()
    assert res["deleted_count"] == 1 and not f.exists()


def test_folder_explorer_deletes_respect_exclusions(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    keep = folder / "keep.txt"
    keep.write_text("x")
    storage.set_exclusions([str(keep)])
    res = client.post("/api/filesystem/delete-paths", json={"paths": [str(keep)], "permanent": True,
                                                             "confirm_text": "DELETE"}).json()
    assert res["deleted_count"] == 0
    res = client.post("/api/filesystem/delete-folder", json={"path": str(folder), "permanent": True,
                                                             "confirm_text": "DELETE"}).json()
    assert res["deleted_count"] == 0 and keep.exists()


def test_paths_inside_reports_rules_for_things_in_a_folder():
    m = ExclusionMatcher(["C:\\Work\\proj\\keep.txt", "C:\\Work\\projector", "*.iso"])
    assert m.paths_inside("c:/work/proj") == ["C:\\Work\\proj\\keep.txt"]
    assert m.paths_inside("C:\\Work\\proj\\keep.txt") == []
