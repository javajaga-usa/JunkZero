"""Safeguards for personal files: app token, duplicates, cloud folders, backups, drives without a
Recycle Bin, personal and recent files, leftovers, large deletes, restoring and open files."""
import os
import struct
import time
from types import SimpleNamespace

import pytest

from app import main
from app.config import (
    CAT_DUPLICATES, CAT_OLD_DOWNLOADS, CAT_TEMP_JUNK, HOLDING_DIR_NAME, RISK_REVIEW, RISK_SAFE, ScanOptions,
)
from app.engine import cleaner, deletecheck, osinfo, recycle, safeguards, smart, storage
from app.engine.classifier import build_item, classify_item
from app.engine.duplicates import FileCandidate, pick_keeper
from app.engine.inuse import OpenFileCheck, office_lock_file
from app.engine.leftovers import InstalledPrograms, find_leftovers
from app.engine.scanner import FastScanner
from tests.helpers import api_client, offer

client = api_client()
DAY = 86400
OLD = time.time() - 400 * DAY


def _old(path):
    os.utime(path, (OLD, OLD))
    return path


def _scan(target, **options):
    options.setdefault("cloud_folders", [])
    return FastScanner(ScanOptions(target_path=str(target), **options)).run_scan()


# ------------------------------------------------------------- 1. App token

def test_requests_without_the_app_token_are_refused(tmp_path):
    victim = tmp_path / "victim.docx"
    victim.write_text("x")
    offer([victim])
    body = {"items": [{"path": str(victim)}], "permanent": True}
    no_token = api_client(headers={main.TOKEN_HEADER: ""})
    assert no_token.post("/api/clean", json=body).status_code == 403
    other_page = api_client(headers={"Origin": "https://evil.example"})
    assert other_page.post("/api/clean", json=body).status_code == 403
    assert other_page.post("/api/filesystem/delete-folder", json={"path": str(tmp_path)}).status_code == 403
    assert victim.exists()
    # Reading is still fine, and the page itself gets the token
    assert no_token.get("/api/history").status_code == 200
    assert f'content="{main.API_TOKEN}"' in no_token.get("/").text


def test_clean_only_accepts_items_from_the_scan_results(tmp_path):
    listed, other = tmp_path / "a.tmp", tmp_path / "notes.txt"
    listed.write_text("x")
    other.write_text("x")
    offer([listed])
    res = client.post("/api/clean", json={"permanent": True, "items": [{"path": str(listed)}, {"path": str(other)}]})
    assert res.json()["deleted_count"] == 1 and res.json()["failed_count"] == 1
    assert "scan again" in res.json()["errors"][0]["error"]
    assert not listed.exists() and other.exists()


# ------------------------------------------------------------- 2. Duplicates

def test_keeper_prefers_personal_folders_over_downloads_and_temp():
    docs = FileCandidate("/home/a/Documents/report.pdf", "report.pdf", 10, 100.0)
    dl = FileCandidate("/home/a/Downloads/report.pdf", "report.pdf", 10, 200.0)
    tmp = FileCandidate("/home/a/AppData/Local/Temp/report.pdf", "report.pdf", 10, 300.0)
    assert pick_keeper([dl, tmp, docs]) is docs
    assert pick_keeper([dl, tmp]) is dl


def test_scanner_keeps_the_documents_copy(tmp_path):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Downloads").mkdir()
    data = os.urandom(2 * 1024 * 1024)
    for folder in ("Documents", "Downloads"):
        (tmp_path / folder / "photo.raw").write_bytes(data)
    _old(tmp_path / "Documents" / "photo.raw")  # The newer copy is the one in Downloads
    dups = [i for i in _scan(tmp_path, include_duplicates=True) if i.category == CAT_DUPLICATES]
    assert [d.path for d in dups] == [str(tmp_path / "Downloads" / "photo.raw")]
    assert "the copy in Documents is kept" in dups[0].reason
    assert dups[0].duplicate_paths == [str(tmp_path / "Documents" / "photo.raw")]


def test_the_last_copy_of_a_duplicate_is_never_deleted(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    items = [
        {"path": str(a), "category": CAT_DUPLICATES, "size_bytes": 4, "duplicate_paths": [str(b)]},
        {"path": str(b), "category": CAT_DUPLICATES, "size_bytes": 4, "duplicate_paths": [str(a)]},
    ]
    result = cleaner.delete_items(items, permanent=True)
    assert result.deleted_count == 1 and not a.exists() and b.exists()
    assert "no other copy" in result.errors[0]["error"]


def test_a_duplicate_is_kept_when_the_other_copy_changed(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"same")
    b.write_bytes(b"edit")
    item = {"path": str(a), "category": CAT_DUPLICATES, "size_bytes": 4, "duplicate_paths": [str(b)]}
    assert cleaner.delete_items([item], permanent=True).deleted_count == 0
    assert a.exists()


# ------------------------------------------------------------- 3. Cloud folders

def test_items_in_cloud_folders_are_never_preselected(tmp_path):
    onedrive = tmp_path / "OneDrive"
    onedrive.mkdir()
    temp = onedrive / "Temp"
    temp.mkdir()
    (temp / "y.tmp").write_text("y")
    _old(temp / "y.tmp")
    items = _scan(tmp_path, cloud_folders=[(str(onedrive), "OneDrive")], include_empty_folders=False)
    item = next(i for i in items if i.name == "y.tmp")
    assert item.cloud_provider == "OneDrive" and item.selected is False and item.risk_level == RISK_REVIEW
    assert "also removes it from the cloud" in item.reason
    assert item.score < smart.SCORE_DELETE


def test_cloud_folders_are_found_by_name_and_env(tmp_path):
    home = tmp_path / "home"
    for name in ("OneDrive - Contoso", "Dropbox", "Boxes", "Google Drive"):
        (home / name).mkdir(parents=True)
    found = dict((os.path.basename(p), label) for p, label in safeguards.cloud_folders({"HOME": str(home)}))
    assert found == {"OneDrive - Contoso": "OneDrive", "Dropbox": "Dropbox", "Google Drive": "Google Drive"}


def test_online_only_files_are_never_listed_or_opened(tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    temp.mkdir()
    for name, text in (("online.tmp", "online"), ("local.tmp", "x")):
        (temp / name).write_text(text)
        _old(temp / name)
    original = osinfo.is_online_only
    # Told apart by size: Windows scandir results carry no inode number
    monkeypatch.setattr(osinfo, "is_online_only", lambda st: st.st_size == len("online"))
    names = {i.name for i in _scan(tmp_path, include_empty_folders=False)}
    assert names == {"local.tmp"}
    monkeypatch.setattr(osinfo, "is_online_only", original)
    assert osinfo.is_online_only(SimpleNamespace(st_file_attributes=0x400000)) is True
    assert osinfo.is_online_only(SimpleNamespace(st_file_attributes=0x20)) is False


# ------------------------------------------------------------- 4. Backups and virtual machines

def test_backup_folders_and_vm_disks_are_never_listed(tmp_path):
    for folder in ("FileHistory", "WindowsImageBackup", "Backups.backupdb"):
        (tmp_path / folder / "sub").mkdir(parents=True)
        (tmp_path / folder / "sub" / "old.tmp").write_text("x")
    for name in ("win.vhdx", "linux.qcow2", "disk.vmdk", "c-drive.tib", "image.mrimg", "ubuntu.iso"):
        (tmp_path / name).write_bytes(b"x" * 10)
        _old(tmp_path / name)
    items = _scan(tmp_path)
    assert {i.name for i in items} == {"ubuntu.iso"}


def test_deletes_inside_backup_folders_are_blocked(tmp_path):
    target = tmp_path / "FileHistory" / "me" / "a.txt"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    result = cleaner.delete_items([{"path": str(target), "size_bytes": 1}], permanent=True)
    assert result.deleted_count == 0 and "backup folder" in result.errors[0]["error"]


# ------------------------------------------------------------- 5. Drives without a Recycle Bin

def test_items_on_drives_without_a_recycle_bin_are_set_aside_and_restored(tmp_path, monkeypatch):
    drive = tmp_path / "usb"
    (drive / "photos").mkdir(parents=True)
    item_path = drive / "photos" / "old.tmp"
    item_path.write_text("keep me safe")
    monkeypatch.setattr(recycle, "has_recycle_bin", lambda p: False)
    monkeypatch.setattr(recycle, "mount_point", lambda p: str(drive))
    monkeypatch.setattr(cleaner.send2trash, "send2trash", lambda p: pytest.fail("must not use the Recycle Bin"))
    offer([item_path])

    res = client.post("/api/clean", json={"items": [{"path": str(item_path)}]}).json()
    assert res["deleted_count"] == 1 and not item_path.exists()
    held = res["held"][str(item_path)]
    assert HOLDING_DIR_NAME in held and open(held).read() == "keep me safe"

    record = client.get("/api/history").json()["history"][0]
    assert record["held"] == {str(item_path): held}
    out = client.post("/api/history/restore", json={"timestamp": record["timestamp"]}).json()
    assert out["restored"] == [str(item_path)] and item_path.read_text() == "keep me safe"
    assert out["history"][0]["restored"] == [str(item_path)]


def test_held_items_are_removed_after_seven_days(tmp_path):
    batch = recycle.new_holding_batch(str(tmp_path), now=time.time() - 8 * DAY)
    fresh = recycle.new_holding_batch(str(tmp_path))
    assert recycle.purge_holding() == 1
    assert not os.path.exists(batch) and os.path.exists(fresh)
    assert storage.load_settings()["holding_roots"] == [str(tmp_path)]


def test_preview_says_which_items_have_no_recycle_bin(tmp_path, monkeypatch):
    f = tmp_path / "a.tmp"
    f.write_text("x")
    monkeypatch.setattr(recycle, "has_recycle_bin", lambda p: False)
    assert deletecheck.summarize([{"path": str(f)}], permanent=False)["no_recycle_count"] == 1
    assert deletecheck.summarize([{"path": str(f)}], permanent=True)["no_recycle_count"] == 0


def test_windows_drive_types(monkeypatch):
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    monkeypatch.setattr(recycle, "mount_point", lambda p: "E:\\")
    for kind, expected in ((3, True), (2, False), (4, False), (5, False)):
        monkeypatch.setattr(recycle, "_windows_drive_type", lambda root, k=kind: k)
        assert recycle.has_recycle_bin("E:\\photos\\a.jpg") is expected


# ------------------------------------------------------------- 6-8. Personal, recent and stray temp files

def test_personal_and_recent_files_are_never_preselected_or_marked_delete():
    guard = safeguards.Safeguards(cloud=[], now=time.time())
    scorer = smart.SmartScorer({"deleted": {f"{CAT_OLD_DOWNLOADS}|.pdf": 9}})
    pdf = build_item("/home/a/Downloads/tax.pdf", "tax.pdf", CAT_OLD_DOWNLOADS, 1, OLD, RISK_REVIEW, "r")
    guard.apply(scorer.apply(pdf))
    assert pdf.personal and pdf.selected is False and pdf.recommendation != "Delete"
    assert "personal file" in pdf.score_reasons[-1]

    fresh = build_item("/tmp/x/a.tmp", "a.tmp", CAT_TEMP_JUNK, 1, time.time() - 60, RISK_SAFE, "r")
    guard.apply(scorer.apply(fresh))
    assert fresh.selected is False and fresh.risk_level == RISK_REVIEW and "24 hours" in fresh.score_reasons[-1]

    old = build_item("/tmp/x/b.tmp", "b.tmp", CAT_TEMP_JUNK, 1, OLD, RISK_SAFE, "r")
    guard.apply(scorer.apply(old))
    assert old.selected is True and old.recommendation == "Delete"


def test_thumbs_db_is_not_a_personal_file():
    assert not safeguards.is_personal_file("Thumbs.db")
    assert safeguards.is_personal_file("holiday.JPG") and safeguards.is_personal_file("slot1.sav")


@pytest.mark.parametrize("path, listed", [
    ("C:/Users/a/Documents/radio.log", False),
    ("C:/Users/a/Documents/scratch.tmp", False),
    ("C:/Users/a/AppData/Local/Temp/x.tmp", True),
    ("C:/Users/a/.npm/_logs/debug.log", True),
    ("C:/Users/a/project/build/out.log", True),
    ("C:/Users/a/Pictures/Thumbs.db", True),
    ("/Users/a/Library/Caches/app/x.log", True),
])
def test_temp_style_files_outside_temp_folders_are_not_listed(path, listed):
    item = classify_item(path, path.rsplit("/", 1)[-1], 10, OLD, False, ScanOptions(target_path="."))
    assert (item is not None) is listed


# ------------------------------------------------------------- 9. Program leftovers

def test_leftovers_holding_personal_files_are_left_out(tmp_path):
    now = time.time()
    for name, extra in (("OldGame", "slot1.sav"), ("OldTool", "cache.bin")):
        folder = tmp_path / name
        folder.mkdir()
        (folder / extra).write_text("x")
        for p in (folder / extra, folder):
            os.utime(p, (now - 400 * DAY, now - 400 * DAY))
    found = find_leftovers([str(tmp_path)], InstalledPrograms(["Something Else Entirely"]), now=now)
    assert [l.name for l in found] == ["OldTool"]


# ------------------------------------------------------------- 10. Large deletes

def test_personal_files_need_delete_typed(tmp_path):
    photo = tmp_path / "Downloads" / "old.jpg"
    photo.parent.mkdir()
    photo.write_text("x")
    offer([photo], category=CAT_OLD_DOWNLOADS)
    preview = client.post("/api/delete-preview", json={"paths": [str(photo)]}).json()
    assert preview["needs_typed_confirm"] and preview["personal_examples"] == ["old.jpg"]
    body = {"items": [{"path": str(photo)}], "permanent": True}
    assert client.post("/api/clean", json=body).status_code == 400
    assert photo.exists()
    assert client.post("/api/clean", json={**body, "confirm_text": "delete"}).json()["deleted_count"] == 1


def test_large_deletes_need_delete_typed(tmp_path):
    entries = [{"path": str(tmp_path / f"{n}.tmp"), "size_bytes": 1} for n in range(1001)]
    assert deletecheck.summarize(entries, True)["needs_typed_confirm"]
    big = [{"path": str(tmp_path / "a.iso"), "size_bytes": 6 * 1024 ** 3}]
    assert deletecheck.summarize(big, True)["needs_typed_confirm"]
    small = [{"path": str(tmp_path / "a.tmp"), "size_bytes": 10}]
    assert not deletecheck.summarize(small, True)["needs_typed_confirm"]


def test_folder_explorer_shows_whats_inside_a_folder(tmp_path):
    folder = tmp_path / "Taxes"
    (folder / "2024").mkdir(parents=True)
    (folder / "2024" / "return.pdf").write_text("x" * 100)
    (folder / "notes.ini").write_text("x")
    summary = client.post("/api/delete-preview", json={"paths": [str(folder)], "source": "explorer"}).json()
    assert summary["count"] == 2 and summary["personal_count"] == 1 and summary["needs_typed_confirm"]
    assert {t["type"] for t in summary["types"]} == {".pdf", ".ini"}
    assert client.post("/api/filesystem/delete-folder", json={"path": str(folder), "permanent": True}).status_code == 400
    assert folder.exists()
    res = client.post("/api/filesystem/delete-folder", json={"path": str(folder), "permanent": True, "confirm_text": "DELETE"})
    assert res.json()["deleted_count"] == 1 and not folder.exists()


def test_explorer_deletes_go_through_their_own_endpoint(tmp_path):
    f = tmp_path / "scratch.ini"
    f.write_text("x")
    res = client.post("/api/filesystem/delete-paths", json={"paths": [str(f)], "permanent": True})
    assert res.json()["deleted_count"] == 1 and not f.exists()


# ------------------------------------------------------------- 11. Restoring from the Recycle Bin / Trash

def _i_file(path, version, when):
    filetime = int((when + 11644473600) * 1e7)
    if version == 1:
        raw = path.encode("utf-16-le").ljust(520, b"\0")
        return struct.pack("<qqq", 1, 5, filetime) + raw
    raw = (path + "\0").encode("utf-16-le")
    return struct.pack("<qqqi", 2, 5, filetime, len(path) + 1) + raw


@pytest.mark.parametrize("version", [1, 2])
def test_windows_recycle_bin_records_are_read(version):
    path, when = "C:\\Users\\a\\Documents\\report.docx", 1_760_000_000.0
    assert recycle.parse_recycle_info(_i_file(path, version, when)) == (path, pytest.approx(when))


def test_windows_recycle_bin_lookup_finds_the_newest_match(tmp_path):
    user_bin = tmp_path / "$Recycle.Bin" / "S-1-5-21"
    user_bin.mkdir(parents=True)
    original = "C:\\Users\\a\\a.tmp"
    for suffix, when in (("ABC.tmp", 1000.0), ("DEF.tmp", 2000.0)):
        (user_bin / f"$I{suffix}").write_bytes(_i_file(original, 2, when))
        (user_bin / f"$R{suffix}").write_text(suffix)
    found = recycle.find_in_windows_recycle_bin(original, since=500, bin_root=str(tmp_path / "$Recycle.Bin"))
    assert found == (str(user_bin / "$RDEF.tmp"), [str(user_bin / "$IDEF.tmp")])
    assert recycle.find_in_windows_recycle_bin(original, since=3000, bin_root=str(tmp_path / "$Recycle.Bin")) is None


def test_freedesktop_trash_items_are_restored(tmp_path, monkeypatch):
    trash = tmp_path / "Trash"
    (trash / "info").mkdir(parents=True)
    (trash / "files").mkdir()
    original = tmp_path / "work" / "my file.tmp"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    (trash / "info" / "my file.tmp.trashinfo").write_text(
        f"[Trash Info]\nPath={str(original).replace(' ', '%20')}\nDeletionDate={stamp}\n")
    (trash / "files" / "my file.tmp").write_text("back")
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    monkeypatch.setattr(recycle, "_xdg_trash_dirs", lambda p: [(str(trash), "")])
    out = recycle.restore_cleanup({"mode": "recycle_bin", "timestamp": time.time(), "paths": [str(original)]})
    assert out == {"restored": [str(original)], "errors": []}
    assert original.read_text() == "back" and not (trash / "info" / "my file.tmp.trashinfo").exists()


def test_mac_trash_matches_renamed_items(tmp_path):
    (tmp_path / "report 2.pdf").write_text("x")
    (tmp_path / "other.pdf").write_text("x")
    found = recycle.find_in_mac_trash("/Users/a/Documents/report.pdf", time.time() - 60, trash=str(tmp_path))
    assert found == (str(tmp_path / "report 2.pdf"), [])


def test_restore_never_overwrites_and_skips_permanent_deletes(tmp_path):
    existing = tmp_path / "a.txt"
    existing.write_text("new file")
    out = recycle.restore_cleanup({"mode": "recycle_bin", "timestamp": time.time(), "paths": [str(existing)]})
    assert out["restored"] == [] and "already" in out["errors"][0]["error"]
    assert existing.read_text() == "new file"
    assert recycle.restore_cleanup({"mode": "permanent", "paths": [str(existing)]})["restored"] == []


# ------------------------------------------------------------- 12. Open files

def test_files_open_in_office_or_another_program_are_skipped(tmp_path):
    doc = tmp_path / "Quarterly report.docx"
    doc.write_text("x")
    (tmp_path / "~$arterly report.docx").write_text("lock")
    assert office_lock_file(str(doc))
    busy = tmp_path / "busy.tmp"
    busy.write_text("x")
    check = OpenFileCheck(open_files={str(busy)})
    result = cleaner.delete_items(
        [{"path": str(doc), "size_bytes": 1}, {"path": str(busy), "size_bytes": 1}, {"path": str(tmp_path), "size_bytes": 1}],
        permanent=True, open_check=check)
    assert result.deleted_count == 0 and doc.exists() and busy.exists()
    assert [e["error"] for e in result.errors] == [
        "Skipped: open in Office or LibreOffice (close it and try again)",
        "Skipped: open in another program (close it and try again)",
        "Skipped: a file inside it is open in another program (close it and try again)",
    ]


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc")
def test_files_held_open_by_another_process_are_found(tmp_path):
    import subprocess
    import sys
    target = tmp_path / "held.tmp"
    target.write_text("x")
    proc = subprocess.Popen([sys.executable, "-c", f"f = open({str(target)!r}); import time; time.sleep(30)"])
    try:
        for _ in range(50):
            if OpenFileCheck().reason(str(target), False):
                break
            time.sleep(0.1)
        assert OpenFileCheck().reason(str(target), False) == "open in another program"
    finally:
        proc.kill()
