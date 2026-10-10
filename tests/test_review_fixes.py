"""Safety fixes from the post-Round-4 review: Git folders, personal folders, ambiguous file types, local server."""
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import CAT_EMPTY_FOLDERS, RISK_REVIEW, RISK_SAFE, ScanOptions
from app.engine.classifier import classify_item
from app.engine.cleaner import delete_items, in_vcs_folder
from app.engine.inspector import inspect_path_hierarchy
from app.engine.locations import is_protected_user_folder
from app.engine.scanner import FastScanner
from app.main import app, pick_port


def _scan(path, **kwargs):
    return FastScanner(ScanOptions(target_path=str(path), **kwargs)).run_scan()


def test_git_internal_empty_folders_are_never_flagged(tmp_path):
    # After "git gc" a repo's refs live in packed-refs and .git/refs only holds empty folders
    repo = tmp_path / "repo"
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo / ".git" / "refs" / "tags").mkdir()
    (repo / ".git" / "branches").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".svn" / "tmp").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hi')")
    (repo / "unused").mkdir()

    items = _scan(tmp_path)

    assert [i.name for i in items] == ["unused"]
    assert not any(".git" in i.path or ".svn" in i.path for i in items)


def test_empty_folders_are_not_preselected(tmp_path):
    (tmp_path / "empty").mkdir()
    [item] = [i for i in _scan(tmp_path) if i.category == CAT_EMPTY_FOLDERS]
    assert item.selected is False


def test_cleaner_refuses_anything_in_a_vcs_folder(tmp_path):
    refs = tmp_path / "repo" / ".git" / "refs"
    refs.mkdir(parents=True)
    result = delete_items([
        {"path": str(refs), "size_bytes": 0, "category": CAT_EMPTY_FOLDERS},
        {"path": str(refs.parent), "size_bytes": 0},
    ], permanent=True)
    assert result.deleted_count == 0
    assert all("version-control" in e["error"] for e in result.errors)
    assert refs.exists()
    assert in_vcs_folder(r"C:\proj\.Git\objects") and not in_vcs_folder(r"C:\proj\gitstuff")


def test_personal_folders_are_protected(tmp_path, monkeypatch):
    home = tmp_path / "Users" / "me"
    (home / "Documents" / "old").mkdir(parents=True)
    (home / "Desktop").mkdir()
    (tmp_path / "Users" / "Public").mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)

    for path in (home, home / "Documents", home / "Desktop", tmp_path / "Users", tmp_path / "Users" / "Public"):
        assert is_protected_user_folder(str(path)), path
    assert not is_protected_user_folder(str(home / "Documents" / "old"))

    result = delete_items([{"path": str(home / "Documents"), "size_bytes": 0}], permanent=True)
    assert result.deleted_count == 0 and "personal folder" in result.errors[0]["error"]
    assert (home / "Documents").exists()

    # Inside a personal folder things stay deletable
    result = delete_items([{"path": str(home / "Documents" / "old"), "size_bytes": 0}], permanent=True)
    assert result.deleted_count == 1

    view = inspect_path_hierarchy(str(home / "Desktop"))
    assert view.is_current_deletable is False
    assert not any(c.is_deletable for c in view.breadcrumbs if c.path in (str(home), str(home / "Desktop")))


@pytest.mark.parametrize("path, risk", [
    ("C:/Users/a/models/car.obj", RISK_REVIEW),
    ("C:/proj/x64/Debug/main.obj", RISK_SAFE),
    ("C:/Users/a/science/1abc.pdb", RISK_REVIEW),
    ("C:/proj/bin/Debug/app.pdb", RISK_SAFE),
    ("C:/Users/a/thesis.docx.bak", RISK_REVIEW),
    ("C:/Users/a/settings.old", RISK_REVIEW),
    ("C:/Users/a/App.csproj.user", RISK_REVIEW),
    ("C:/Users/a/cache.tmp", RISK_SAFE),
    ("C:/Users/a/Main.class", RISK_SAFE),
])
def test_ambiguous_file_types_need_review(path, risk):
    item = classify_item(path, path.rsplit("/", 1)[-1], 10, 1.0, False, ScanOptions(target_path="."))
    assert item.risk_level == risk
    assert item.selected is (risk == RISK_SAFE)


def test_api_rejects_unknown_host_header():
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/history").status_code == 200
    assert client.get("/api/history", headers={"host": "attacker.example:8000"}).status_code == 400


def test_pick_port_falls_back_when_preferred_port_is_taken():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        port = pick_port(taken)
        assert port != taken and port > 0


def test_icons_are_bundled_locally():
    html = (Path(__file__).resolve().parent.parent / "app" / "ui" / "index.html").read_text(encoding="utf-8")
    assert "unpkg.com" not in html
    assert "/static/vendor/lucide-" in html
