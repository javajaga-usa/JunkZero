"""Old Downloads category, Largest Files view and remembered preferences."""
import os
import time

from app import main
from app.config import CAT_INSTALLERS, CAT_OLD_DOWNLOADS, ScanOptions
from app.engine.locations import downloads_folder
from app.engine.scanner import FastScanner
from app.engine.space import largest_items
from app.main import app
from tests.helpers import api_client

client = api_client()
DAY = 86400


def _age(path, days):
    t = time.time() - days * DAY
    os.utime(path, (t, t))


def _scan(**kwargs):
    return FastScanner(ScanOptions(**kwargs)).run_scan()


def _downloads_scan(downloads, **kwargs):
    return _scan(target_path=str(downloads.parent), include_old_downloads=True,
                 downloads_dirs=[str(downloads)], include_empty_folders=False, **kwargs)


# ------------------------------------------------------------- Old Downloads

def _fake_ctime(monkeypatch, days):
    """On Linux st_ctime can't be set; pretend files were created `days` ago."""
    real = FastScanner._old_download_item

    def patched(self, path, name, size, stat):
        fake = os.stat_result((*stat[:7], stat.st_atime, stat.st_mtime, time.time() - days * DAY))
        return real(self, path, name, size, fake)

    monkeypatch.setattr(FastScanner, "_old_download_item", patched)


def test_old_downloads_flags_untouched_files_for_review(tmp_path, monkeypatch):
    _fake_ctime(monkeypatch, 400)
    downloads = tmp_path / "Downloads"
    (downloads / "sub").mkdir(parents=True)
    old, fresh, nested = downloads / "report.pdf", downloads / "new.pdf", downloads / "sub" / "photo.jpg"
    for p in (old, fresh, nested):
        p.write_bytes(b"x" * 10)
    _age(old, 120)
    _age(nested, 200)
    _age(fresh, 10)
    outside = tmp_path / "Documents" / "old.pdf"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    _age(outside, 500)

    items = _downloads_scan(downloads)
    flagged = sorted(i.path for i in items if i.category == CAT_OLD_DOWNLOADS)

    assert flagged == sorted([str(old), str(nested)])
    assert all(i.risk_level == "Review Recommended" and not i.selected
               for i in items if i.category == CAT_OLD_DOWNLOADS)


def test_recently_copied_file_with_old_mtime_is_not_flagged(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    f = downloads / "copied.pdf"
    f.write_bytes(b"x")
    _age(f, 365)  # Real st_ctime is "now", so the file counts as recently added
    assert [i for i in _downloads_scan(downloads) if i.category == CAT_OLD_DOWNLOADS] == []


def test_old_downloads_off_by_default_and_other_categories_win(tmp_path, monkeypatch):
    _fake_ctime(monkeypatch, 400)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "notes.txt").write_bytes(b"x")
    (downloads / "setup_x64.exe").write_bytes(b"x")
    for p in downloads.iterdir():
        _age(p, 300)

    default_scan = _scan(target_path=str(tmp_path), downloads_dirs=[str(downloads)])
    assert all(i.category != CAT_OLD_DOWNLOADS for i in default_scan)

    cats = {i.name: i.category for i in _downloads_scan(downloads)}
    assert cats["setup_x64.exe"] == CAT_INSTALLERS
    assert cats["notes.txt"] == CAT_OLD_DOWNLOADS


def test_old_download_days_and_min_size_are_respected(tmp_path, monkeypatch):
    _fake_ctime(monkeypatch, 400)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    small, big = downloads / "small.pdf", downloads / "big.pdf"
    small.write_bytes(b"x")
    big.write_bytes(b"x" * 2048)
    for p in (small, big):
        _age(p, 40)

    assert [i for i in _downloads_scan(downloads) if i.category == CAT_OLD_DOWNLOADS] == []
    items = _downloads_scan(downloads, old_download_days=30, min_file_size_bytes=1024)
    assert [i.name for i in items if i.category == CAT_OLD_DOWNLOADS] == ["big.pdf"]


def test_downloads_folder_from_profile(tmp_path):
    assert downloads_folder({"USERPROFILE": str(tmp_path)}) is None
    (tmp_path / "Downloads").mkdir()
    assert downloads_folder({"USERPROFILE": str(tmp_path)}) == str(tmp_path / "Downloads")


def test_scan_api_passes_old_downloads_options(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "downloads_folder", lambda: str(tmp_path))
    monkeypatch.setattr(main.FastScanner, "run_scan", lambda self: seen.update(opts=self.options) or [])
    res = client.post("/api/scan/start", json={
        "target_path": str(tmp_path), "include_old_downloads": True, "old_download_days": 30,
    })
    assert res.status_code == 200
    for _ in range(50):
        if "opts" in seen:
            break
        time.sleep(0.02)
    opts = seen["opts"]
    assert (opts.include_old_downloads, opts.old_download_days, opts.downloads_dirs) == (True, 30, [str(tmp_path)])
    assert client.post("/api/scan/start", json={"target_path": str(tmp_path), "old_download_days": 0}).status_code == 422


# ------------------------------------------------------------- Largest files & folders

def test_largest_items_ranks_files_and_totals_folders(tmp_path):
    (tmp_path / "big" / "deep").mkdir(parents=True)
    (tmp_path / "small").mkdir()
    (tmp_path / "big" / "deep" / "huge.bin").write_bytes(b"x" * 5000)
    (tmp_path / "big" / "mid.bin").write_bytes(b"x" * 3000)
    (tmp_path / "small" / "tiny.bin").write_bytes(b"x" * 10)
    (tmp_path / "loose.bin").write_bytes(b"x" * 4000)

    result = largest_items(str(tmp_path), file_limit=2)

    assert [f["name"] for f in result["files"]] == ["huge.bin", "loose.bin"]
    assert [(f["name"], f["size_bytes"], f["file_count"]) for f in result["folders"]] == [
        ("big", 8000, 2), ("small", 10, 1),
    ]
    assert result["total_bytes"] == 12010
    assert result["file_count"] == 4
    assert result["loose_files_bytes"] == 4000
    assert result["complete"] is True


def test_largest_items_skips_exclusions_and_protected_folders(tmp_path):
    for name in ("keep", "Windows", "skipme"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "f.bin").write_bytes(b"x" * 100)
    (tmp_path / "keep" / "movie.iso").write_bytes(b"x" * 100)

    result = largest_items(str(tmp_path), exclusions=[str(tmp_path / "skipme"), "*.iso"])

    assert [f["name"] for f in result["folders"]] == ["keep"]
    assert result["total_bytes"] == 100
    assert all("movie.iso" not in f["path"] for f in result["files"])


def test_largest_items_endpoint_is_read_only(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 10)
    res = client.post("/api/space/largest", json={"path": str(tmp_path)})
    assert res.status_code == 200
    assert res.json()["files"][0]["name"] == "a.bin"
    assert (tmp_path / "a.bin").exists()
    assert client.post("/api/space/largest", json={"path": str(tmp_path / "missing")}).status_code == 400


# ------------------------------------------------------------- Preferences

def test_preferences_round_trip_and_unknown_options_dropped():
    assert client.get("/api/preferences").json() == {"preferences": {}}
    res = client.put("/api/preferences", json={
        "theme": "light", "target_path": "D:\\Data",
        "scan_options": {"include_duplicates": True, "include_old_downloads": False, "delete_everything": True},
    })
    assert res.status_code == 200
    prefs = client.get("/api/preferences").json()["preferences"]
    assert prefs == {
        "theme": "light", "target_path": "D:\\Data",
        "scan_options": {"include_duplicates": True, "include_old_downloads": False},
    }
    assert client.put("/api/preferences", json={"theme": "neon"}).status_code == 422
