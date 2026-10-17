"""Each disk says what a delete does there (Trash, holding folder, read-only), and the delete does it."""
import pytest

from app.engine import cleaner, deletecheck, osinfo, recycle
from tests.helpers import api_client


def _mac(monkeypatch, mounts, root_of):
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(recycle, "_mac_mounts", lambda: mounts)
    monkeypatch.setattr(recycle, "mount_point", root_of)


@pytest.fixture
def mac_mixed(tmp_path, monkeypatch):
    """A Mac with the startup disk, an exFAT stick, a network share and a read-only NTFS disk."""
    disks = {name: tmp_path / name for name in ("MacHD", "J Red", "Share", "WinDisk")}
    for d in disks.values():
        d.mkdir()
    roots = {str(disks["MacHD"]): "/"}
    mounts = {"/": "apfs", str(disks["J Red"]): "exfat", str(disks["Share"]): "smbfs", str(disks["WinDisk"]): "ntfs"}

    def root_of(p):
        for d in disks.values():
            if str(p).startswith(str(d)):
                return roots.get(str(d), str(d))
        return "/"
    _mac(monkeypatch, mounts, root_of)
    real = recycle.is_read_only
    monkeypatch.setattr(recycle, "is_read_only", lambda p: str(p).startswith(str(disks["WinDisk"])) or real(p))
    return disks


def test_disk_info_names_the_disk_its_format_and_what_happens(mac_mixed):
    boot = recycle.disk_info(str(mac_mixed["MacHD"] / "a.tmp"))
    assert (boot["name"], boot["fs_label"], boot["destination"]) == ("Macintosh HD", "APFS", "trash")
    red = recycle.disk_info(str(mac_mixed["J Red"] / "a.tmp"))
    assert (red["name"], red["fs_label"], red["destination"]) == ("J Red", "exFAT", "holding")
    assert "exFAT disks macOS may erase" in red["reason"]
    share = recycle.disk_info(str(mac_mixed["Share"] / "a.tmp"))
    assert share["destination"] == "holding" and "network drives have no Trash" in share["reason"]
    ntfs = recycle.disk_info(str(mac_mixed["WinDisk"] / "a.tmp"))
    assert ntfs["destination"] == "read_only" and "macOS can only read NTFS" in ntfs["reason"]


def test_permanent_mode_erases_except_on_read_only_disks(mac_mixed):
    red = str(mac_mixed["J Red"] / "a.tmp")
    win = str(mac_mixed["WinDisk"] / "a.tmp")
    assert recycle.item_destination(red, 1, True)[0] == "erase"
    assert recycle.item_destination(win, 1, True)[0] == "read_only"


def test_preview_lists_each_disk_and_its_outcome(mac_mixed):
    paths = []
    for disk, n in (("MacHD", 2), ("J Red", 3), ("WinDisk", 1)):
        for i in range(n):
            f = mac_mixed[disk] / f"f{i}.tmp"
            f.write_text("x")
            paths.append({"path": str(f), "size_bytes": 1})
    summary = deletecheck.summarize(paths, permanent=False)
    rows = {(d["name"], d["destination"]): d["count"] for d in summary["disks"]}
    assert rows == {("Macintosh HD", "trash"): 2, ("J Red", "holding"): 3, ("WinDisk", "read_only"): 1}
    assert summary["no_recycle_count"] == 3 and summary["read_only_count"] == 1


def test_delete_does_what_the_preview_said(mac_mixed, monkeypatch):
    trashed = []
    monkeypatch.setattr(cleaner.send2trash, "send2trash", trashed.append)
    boot = mac_mixed["MacHD"] / "a.tmp"
    red = mac_mixed["J Red"] / "b.tmp"
    win = mac_mixed["WinDisk"] / "c.tmp"
    for f in (boot, red, win):
        f.write_text("x")
    result = cleaner.delete_items([{"path": str(f), "size_bytes": 1} for f in (boot, red, win)], exclusions=[])
    assert trashed == [str(boot)]
    assert list(result.held) == [str(red)] and not red.exists()
    assert win.exists()
    error = next(e["error"] for e in result.errors if e["path"] == str(win))
    assert error.startswith("Can't delete: macOS can only read NTFS disks") and "(WinDisk)" in error


def test_windows_drive_kinds_and_reasons(monkeypatch):
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    monkeypatch.setattr(recycle, "mount_point", lambda p: p[:3])
    monkeypatch.setattr(recycle, "_windows_volume", lambda root: ({"C:\\": "Windows", "E:\\": "SANDISK"}.get(root, ""),
                                                                  "NTFS" if root == "C:\\" else "exFAT", False))
    kinds = {"C:\\": 3, "E:\\": 2, "Z:\\": 4, "D:\\": 5}
    monkeypatch.setattr(recycle, "_windows_drive_type", lambda root: kinds[root])
    monkeypatch.setattr(recycle, "_windows_recycle_settings", lambda root: (None, False))
    c = recycle.disk_info("C:\\Users\\a.tmp")
    assert (c["name"], c["fs_label"], c["destination"]) == ("Windows (C:)", "NTFS", "trash")
    e = recycle.disk_info("E:\\a.tmp")
    assert e["destination"] == "holding" and "USB drives" in e["reason"] and e["name"] == "SANDISK (E:)"
    z = recycle.disk_info("Z:\\a.tmp")
    assert z["destination"] == "holding" and "network drives" in z["reason"]
    d = recycle.disk_info("D:\\a.tmp")
    assert d["destination"] == "read_only"


def test_windows_items_too_big_for_the_recycle_bin_say_why(monkeypatch):
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    monkeypatch.setattr(recycle, "mount_point", lambda p: "C:\\")
    monkeypatch.setattr(recycle, "_windows_volume", lambda root: ("Windows", "NTFS", False))
    monkeypatch.setattr(recycle, "_windows_drive_type", lambda root: 3)
    monkeypatch.setattr(recycle, "_windows_recycle_settings", lambda root: (100, False))
    dest, why = recycle.item_destination("C:\\big.iso", 10_000, False)
    assert dest == "holding" and "would erase it" in why
    assert recycle.item_destination("C:\\small.tmp", 10, False)[0] == "trash"


def test_disk_info_endpoint(tmp_path):
    client = api_client()
    data = client.get("/api/system/disk-info", params={"path": str(tmp_path)}).json()
    assert data["destination"] in ("trash", "holding", "read_only") and data["holding_days"] == 7
    assert client.get("/api/system/disk-info", params={"path": str(tmp_path / "gone")}).status_code == 404
