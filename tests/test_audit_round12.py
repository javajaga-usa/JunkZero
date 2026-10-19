"""Fixes from the full audit before the 1.3.1/1.4.0 release (round 12)."""
import os
from types import SimpleNamespace

import pytest

from app.engine import linux_scheduler, notify, osinfo, scheduler, storage


@pytest.fixture
def linux_os(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(osinfo, "is_linux", lambda: True)


# ---------------------------------------------------------------- Scheduling

def test_systemd_unit_survives_quotes_and_dollars_in_the_path():
    args = ["/home/bob/Bob's $tools/JunkZero", "--mode", "report"]
    service = linux_scheduler.systemd_service(args)
    assert 'ExecStart="/usr/bin/env" ' in service
    assert linux_scheduler.parse_exec_start(service) == args


def test_timer_left_from_a_systemd_boot_is_remade_in_cron(monkeypatch, tmp_path, linux_os):
    """MX Linux booted with systemd once, then with SysVinit: the old timer never runs."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    units = linux_scheduler.unit_dir()
    units.mkdir(parents=True)
    monkeypatch.setattr(scheduler.sys, "frozen", True, raising=False)
    monkeypatch.setattr(scheduler.sys, "executable", "/opt/junkzero/JunkZero")
    wanted = scheduler.launchd_program_args("unused")
    (units / "junkzero-scan.service").write_text(linux_scheduler.systemd_service(wanted))
    (units / "junkzero-scan.timer").write_text(linux_scheduler.systemd_timer("daily", "09:00", "MON"))
    crontab = {"text": ""}

    def fake_run(args, **kw):
        if args[:2] == ["crontab", "-l"]:
            return SimpleNamespace(returncode=0, stdout=crontab["text"], stderr="")
        if args[:2] == ["crontab", "-"]:
            crontab["text"] = kw["input"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(linux_scheduler, "_run", fake_run)
    monkeypatch.setattr(linux_scheduler, "systemd_user_available", lambda: False)
    monkeypatch.setattr(linux_scheduler, "cron_available", lambda: True)
    storage.update_settings(schedule={"enabled": True, "frequency": "daily", "time": "09:00", "day": "MON"})
    assert scheduler.ensure_task_current() is True
    assert linux_scheduler.CRON_MARKER in crontab["text"]
    assert not (units / "junkzero-scan.timer").exists()


def test_scheduled_scan_keeps_a_custom_data_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("JUNKZERO_DATA_DIR", str(tmp_path / "jz"))
    assert repr(str(storage.data_dir())) in scheduler.launcher_script()
    monkeypatch.setattr(scheduler.sys, "frozen", True, raising=False)
    monkeypatch.setattr(scheduler.sys, "executable", "/opt/junkzero/JunkZero")
    assert scheduler.launchd_program_args("x")[-2:] == ["--data-dir", str(storage.data_dir())]
    assert f'--data-dir "{storage.data_dir()}"' in scheduler.task_action("x")[1]


def test_scheduled_scan_without_a_custom_data_folder_passes_nothing(monkeypatch):
    monkeypatch.delenv("JUNKZERO_DATA_DIR", raising=False)
    assert "JUNKZERO_DATA_DIR" not in scheduler.launcher_script()


@pytest.mark.skipif(os.name == "nt", reason="POSIX session bus")
def test_notification_from_cron_finds_the_desktop(monkeypatch, tmp_path):
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(notify.os.path, "exists", lambda p: False)
    env = notify._desktop_env()
    assert env["DISPLAY"] == ":0"
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/x")
    assert "DISPLAY" not in notify._desktop_env()


# ---------------------------------------------------------------- Deleting and Restore

@pytest.mark.skipif(os.name == "nt", reason="symlinked folders")
def test_open_file_check_sees_files_through_a_linked_folder(tmp_path):
    """A Mac's $TMPDIR is under /var, a link to /private/var, which is what lsof reports."""
    from app.engine.inuse import OpenFileCheck
    real = tmp_path / "private_var"
    (real / "T").mkdir(parents=True)
    (real / "T" / "busy.tmp").write_text("x")
    (tmp_path / "var").symlink_to(real)
    check = OpenFileCheck(open_files={str(real / "T" / "busy.tmp")})
    assert check.reason(str(tmp_path / "var" / "T" / "busy.tmp"), False) == "open in another program"
    assert check.reason(str(tmp_path / "var" / "T"), True) is not None


def test_recycle_bin_limit_uses_the_size_at_delete_time(tmp_path, monkeypatch):
    from app.engine import cleaner, recycle
    folder = tmp_path / "cache"
    folder.mkdir()
    (folder / "big.bin").write_bytes(b"x" * 50_000)
    seen = []

    def destination(path, size, permanent, disk):
        seen.append(size)
        return recycle.DEST_READ_ONLY, "test stop"

    monkeypatch.setattr(recycle, "item_destination", destination)
    cleaner.delete_items([{"path": str(folder), "size_bytes": 10}], open_check=None)
    assert seen and seen[0] >= 50_000


def test_restore_ignores_a_later_delete_from_the_same_place(tmp_path):
    from app.engine import recycle
    trash = tmp_path / "Trash"
    (trash / "info").mkdir(parents=True)
    (trash / "files").mkdir()
    original = str(tmp_path / "cache.db")
    for name, stamp in [("cache.db", "2026-10-01T10:00:00"), ("cache.db.2", "2026-10-05T10:00:00")]:
        (trash / "files" / name).write_text(name)
        (trash / "info" / f"{name}.trashinfo").write_text(
            f"[Trash Info]\nPath={original}\nDeletionDate={stamp}\n")
    import time
    from datetime import datetime
    since = time.mktime(datetime(2026, 10, 1, 9, 58).timetuple())
    until = time.mktime(datetime(2026, 10, 1, 10, 2).timetuple())
    found = recycle.find_in_xdg_trash(original, since, [(str(trash), "")], until=until)
    assert found and found[0].endswith(os.path.join("files", "cache.db"))
    index = {recycle._norm(original): [(since + 60, "first", "i1"), (until + 86400, "later", "i2")]}
    recycle_exists = os.path.lexists
    try:
        recycle.os.path.lexists = lambda p: True
        assert recycle.find_in_windows_recycle_bin(original, since, index=index, until=until)[0] == "first"
    finally:
        recycle.os.path.lexists = recycle_exists


def test_failed_windows_permanent_delete_keeps_the_folder_name(tmp_path, monkeypatch):
    from app.engine import cleaner
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    folder = tmp_path / "build"
    folder.mkdir()
    (folder / "a.o").write_text("x")

    def failing(path, unlock):
        raise OSError("Access is denied")

    monkeypatch.setattr(cleaner, "_rmtree", failing)
    with pytest.raises(OSError):
        cleaner._remove_permanently(str(folder))
    assert folder.is_dir() and (folder / "a.o").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["build"]


def test_purge_keeps_a_drive_added_while_it_ran(tmp_path, monkeypatch):
    from app.engine import recycle
    storage.update_settings(holding_roots=[])
    old_rmtree = recycle.shutil.rmtree
    usb1 = tmp_path / "usb1"
    batch = usb1 / recycle.HOLDING_DIR_NAME / "20200101-000000-abc"
    batch.mkdir(parents=True)
    storage.update_settings(holding_roots=[str(usb1)])
    monkeypatch.setattr(storage, "recorded_holding_batches", lambda: {batch.name: 0})
    monkeypatch.setattr(recycle, "_BATCH_RE", __import__("re").compile(r"(\d{8}-\d{6})"))

    def rmtree_and_new_drive(path, ignore_errors=False):
        old_rmtree(path, ignore_errors=ignore_errors)
        (tmp_path / "usb2" / recycle.HOLDING_DIR_NAME).mkdir(parents=True)
        recycle._remember_holding_root(str(tmp_path / "usb2"))

    monkeypatch.setattr(recycle.shutil, "rmtree", rmtree_and_new_drive)
    recycle.purge_holding()
    assert storage.load_settings()["holding_roots"] == [str(tmp_path / "usb2")]
