"""macOS: only disks whose Trash keeps items get the Trash; others get JunkZero's holding folder."""
import pytest

from app.config import CAT_TEMP_JUNK, ScanOptions
from app.engine import cleaner, osinfo, recycle
from app.engine.scanner import FastScanner

MOUNTS = {
    "/": "apfs",
    "/Volumes/JApple Extreme SSD": "apfs",
    "/Volumes/Old Mac Disk": "hfs",
    "/Volumes/J Red": "exfat",
    "/Volumes/CARD": "msdos",
    "/Volumes/Windows": "ntfs",
    "/Volumes/Share": "smbfs",
}


@pytest.fixture
def mac_disks(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(recycle, "_mac_mounts", lambda: MOUNTS)
    monkeypatch.setattr(recycle, "mount_point",
                        lambda p: max((m for m in MOUNTS if p == m or p.startswith(m.rstrip("/") + "/")), key=len))


@pytest.mark.parametrize("path, trash", [
    ("/Users/jaga/Downloads/a.dmg", True),               # startup disk
    ("/Volumes/JApple Extreme SSD/Downloads/a.zip", True),
    ("/Volumes/Old Mac Disk/a.iso", True),
    ("/Volumes/J Red/Kubuntu/a.tar.gz", False),          # exFAT: Trash may erase at once
    ("/Volumes/CARD/DCIM/a.tmp", False),                 # FAT memory card
    ("/Volumes/Windows/a.tmp", False),
    ("/Volumes/Share/a.tmp", False),                     # network share
])
def test_only_apfs_and_hfs_disks_use_the_trash(mac_disks, path, trash):
    assert recycle.has_recycle_bin(path) is trash
    assert recycle.recycle_bin_takes(path, 10) is trash


def test_a_disk_whose_file_system_cannot_be_told_gets_the_holding_folder(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(recycle, "_mac_mounts", lambda: {})
    monkeypatch.setattr(recycle, "mount_point", lambda p: "/" if p.startswith("/Users") else "/Volumes/X")
    assert recycle.has_recycle_bin("/Users/jaga/a.tmp") is True
    assert recycle.has_recycle_bin("/Volumes/X/a.tmp") is False


def test_mount_lines_are_read_with_fskit_and_spaces_in_names(monkeypatch):
    out = ("/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)\n"
           "/dev/disk5s1 on /Volumes/J Red (exfat, local, nodev, nosuid, noowners, noatime, fskit)\n"
           "//jaga@nas/media on /Volumes/media (smbfs, nodev, nosuid, mounted by jaga)\n")

    class Done:
        stdout = out
    monkeypatch.setattr(recycle.subprocess, "run", lambda *a, **k: Done())
    monkeypatch.setattr(recycle, "_mac_mounts_cache", (0.0, {}))
    assert recycle._mac_mounts() == {"/": "apfs", "/Volumes/J Red": "exfat", "/Volumes/media": "smbfs"}


def test_exfat_items_go_to_the_holding_folder_not_the_trash(tmp_path, monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(recycle, "_mac_mounts", lambda: {str(tmp_path): "exfat"})
    monkeypatch.setattr(recycle, "mount_point", lambda p: str(tmp_path))
    monkeypatch.setattr(cleaner.send2trash, "send2trash", lambda p: pytest.fail("must not use the Trash on exFAT"))
    junk = tmp_path / "Kubuntu" / "old.tar.gz"
    junk.parent.mkdir()
    junk.write_text("x")
    result = cleaner.delete_items([{"path": str(junk), "size_bytes": 1}], exclusions=[])
    assert result.deleted_count == 1
    held = result.held[str(junk)]
    assert "/.JunkZero-holding/" in held.replace("\\", "/")
    assert not junk.exists() and open(held).read() == "x"


def test_ccc_safetynet_is_never_scanned(tmp_path):
    safety = tmp_path / "_CCC SafetyNet" / "2026-10-16 (October 16) 17-15-18"
    safety.mkdir(parents=True)
    (safety / "old.tmp").write_text("x")
    (tmp_path / "other.crdownload").write_text("x")
    items = FastScanner(ScanOptions(target_path=str(tmp_path), cloud_folders=[])).run_scan()
    assert all("_CCC SafetyNet" not in i.path for i in items)
    assert any(i.name == "other.crdownload" for i in items)


def test_ccc_safetynet_is_never_deleted(tmp_path):
    safety = tmp_path / "_CCC SafetyNet"
    safety.mkdir()
    (safety / "kept.doc").write_text("x")
    result = cleaner.delete_items([{"path": str(safety / "kept.doc")}, {"path": str(safety), "is_directory": True}],
                                  permanent=True, exclusions=[])
    assert result.deleted_count == 0 and (safety / "kept.doc").exists()
