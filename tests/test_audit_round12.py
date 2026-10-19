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

    def rmtree_and_new_drive(path, **kw):
        old_rmtree(path, **kw)
        (tmp_path / "usb2" / recycle.HOLDING_DIR_NAME).mkdir(parents=True)
        recycle._remember_holding_root(str(tmp_path / "usb2"))

    monkeypatch.setattr(recycle.shutil, "rmtree", rmtree_and_new_drive)
    recycle.purge_holding()
    assert storage.load_settings()["holding_roots"] == [str(tmp_path / "usb2")]


# ---------------------------------------------------------------- Server and page

def test_folder_holding_listed_junk_is_not_also_a_duplicate_folder(home):
    from app.config import CAT_DUPLICATE_FOLDERS
    from tests.test_round10 import _age_tree, _scan, _write
    for name in ("copyA", "copyB"):
        _write(home / "Downloads" / name / "movie.mp4.part", b"p" * 300_000)
        _write(home / "Downloads" / name / "notes.txt", b"n" * 300_000)
        _age_tree(home / "Downloads" / name)
    _, items = _scan(home, include_duplicates=True)
    assert any(k.endswith("movie.mp4.part") for k in items)
    assert not any(i.category == CAT_DUPLICATE_FOLDERS for i in items.values())


def test_scheduled_report_opened_after_a_scan_can_be_deleted_from(tmp_path):
    from tests.helpers import api_client
    from app import main
    client = api_client()
    junk = tmp_path / "old.tmp"
    junk.write_text("x")
    from app.config import ScanOptions
    main.current_scanner = main.FastScanner(ScanOptions(target_path=str(tmp_path)))
    storage.save_report({"generated_at": 1, "items": [{"path": str(junk), "size_bytes": 1}], "item_count": 1})
    assert client.post("/api/reports/use").status_code == 200
    assert main.current_scanner is None
    assert main._key(str(junk)) in main.scan_results()


def test_show_in_finder_never_opens_a_package(monkeypatch):
    from app import main
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    for p in ("/Users/a/Old.pkg", "/Users/a/App.xcodeproj", "/Users/a/Note.rtfd"):
        assert main.reveal_command(p, False) == ["open", "-R", p]


def test_ai_inspect_is_no_surer_than_the_scanner(tmp_path):
    import time
    from app.engine.ai_advisor import analyze_item
    temp = tmp_path / "AppData" / "Local" / "Temp"
    temp.mkdir(parents=True)
    fresh = temp / "video.mp4.crdownload"
    fresh.write_text("x")
    assert analyze_item(str(fresh)).safety_verdict == "Review Carefully"
    old = temp / "old.tmp"
    old.write_text("x")
    os.utime(old, (time.time() - 5 * 86400,) * 2)
    for name in ("setup.msi", "game.apk"):
        (temp / name).write_text("x")
        assert analyze_item(str(temp / name)).safety_verdict == "Review Carefully", name
    (tmp_path / "Photos.zip").mkdir()
    assert analyze_item(str(tmp_path / "Photos.zip")).safety_verdict == "Review Carefully"


# ---------------------------------------------------------------- Detection

OLD = 1_600_000_000


def _classify(path, size=10, is_dir=False):
    from app.config import ScanOptions
    from app.engine.classifier import classify_item
    return classify_item(str(path), os.path.basename(str(path)), size, OLD, is_dir, ScanOptions(target_path="."))


def test_stray_project_file_in_home_doesnt_make_personal_files_build_output(tmp_path, monkeypatch):
    from app.engine.classifier import _in_build_output_folder
    home = tmp_path / "bob"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    model = home / "Documents" / "Models" / "Release" / "character.obj"
    model.parent.mkdir(parents=True)
    model.write_text("v 0 0 0")
    (home / "package.json").write_text("{}")
    assert not _in_build_output_folder(str(model))
    # A web project's Release folder isn't .NET or C++ build output either
    web = home / "code" / "site"
    (web / "Release").mkdir(parents=True)
    (web / "package.json").write_text("{}")
    assert not _in_build_output_folder(str(web / "Release" / "a.obj"))
    native = home / "code" / "game"
    (native / "x64" / "Release").mkdir(parents=True)
    (native / "game.sln").write_text("")
    assert _in_build_output_folder(str(native / "x64" / "Release" / "a.obj"))


def test_vm_disks_and_backup_images_are_never_listed_as_anything(home):
    from tests.test_round10 import _age_tree, _scan, _write
    data = b"v" * 2_000_000
    _write(home / "Downloads" / "laptop-2024.mrimg", data)
    _write(home / "VMs" / "win11.vhdx", data)
    _write(home / "USB-copy" / "win11.vhdx", data)
    _age_tree(home)
    _, items = _scan(home, include_duplicates=True, include_old_downloads=True)
    assert not any(k.endswith((".mrimg", ".vhdx")) or k in ("VMs", "USB-copy") for k in items), list(items)


def test_timeshift_and_back_in_time_snapshots_are_never_scanned():
    from app.engine.scanner import _in_vcs_or_backup
    assert _in_vcs_or_backup("/media/bob/Data/timeshift/snapshots/2026-10-01/localhost/home/bob/x.log")
    assert _in_vcs_or_backup("/media/bob/Data/backintime/pc/bob/1/20261001/backup/home/bob/x.log")


@pytest.mark.skipif(os.name == "nt", reason="hard links")
def test_hard_linked_folder_copies_are_not_duplicate_folders(home):
    from app.config import CAT_DUPLICATE_FOLDERS
    from tests.test_round10 import _age_tree, _scan, _write
    a = home / "snapshots" / "daily.0"
    for name in ("one.bin", "two.bin"):
        _write(a / name, os.urandom(1_200_000))
    b = home / "snapshots" / "daily.1"
    b.mkdir()
    for name in ("one.bin", "two.bin"):
        os.link(a / name, b / name)
    _age_tree(home / "snapshots")
    _, items = _scan(home, include_duplicates=True)
    assert not any(i.category == CAT_DUPLICATE_FOLDERS for i in items.values())


def test_mac_application_support_is_program_data():
    from app.engine.classifier import _in_app_folder
    assert _in_app_folder("/Users/bob/Library/Application Support/Ableton/Library/Grand Piano.ncw")


def test_exclusions_with_home_shortcuts_match(monkeypatch, tmp_path):
    from tests.helpers import api_client
    from app.engine.exclusions import ExclusionMatcher
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    rules = storage.set_exclusions(["~/Documents/Taxes"])
    assert ExclusionMatcher(rules).matches(os.path.join(str(tmp_path), "Documents", "Taxes", "2019.log"))
    res = api_client().post("/api/exclusions/add", json={"rule": "Documents/Taxes"})
    assert res.status_code == 400 and "full path" in res.json()["detail"]


def test_zipped_disk_image_is_not_a_setup_archive():
    from app.engine.archives import judge_listing
    assert not judge_listing(["sdcard-backup-2023.img"]).is_setup
    assert not judge_listing(["disc.iso", "readme.txt"]).is_setup
    assert judge_listing(["Tool-setup.msi", "readme.txt"]).is_setup


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs a folder the user can't read")
def test_unreadable_subfolder_means_unknown(tmp_path):
    from app.engine import leftovers, projects
    app = tmp_path / "OldNotesApp"
    (app / "notes").mkdir(parents=True)
    (app / "notes" / "diary.docx").write_text("x")
    os.chmod(app / "notes", 0)
    try:
        assert leftovers.holds_personal_files(str(app))
        assert leftovers.newest_change(str(app)) == 0.0
        assert projects.last_activity(str(app)) == 0.0
    finally:
        os.chmod(app / "notes", 0o755)


def test_far_future_date_doesnt_stop_the_scan():
    from app.engine.classifier import format_date
    assert format_date(1e15) == "Unknown"
    assert format_date(0) == "Unknown"


# ---------------------------------------------------------------- Permission problems

needs_user = pytest.mark.skipif(os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0,
                                reason="needs a normal (not root) POSIX account")


@pytest.fixture
def someone_else(monkeypatch):
    """Files this test makes look like another account's: JunkZero runs as a different uid."""
    real = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real + 1)


@needs_user
def test_another_accounts_read_only_file_is_not_listed(tmp_path, someone_else):
    from app.engine import permissions
    f = tmp_path / "a.tmp"
    f.write_text("x")
    f.chmod(0o444)
    assert "belongs to" in permissions.removal_blocker(str(f))


@needs_user
def test_folder_holding_another_accounts_folder_is_not_listed(tmp_path, someone_else):
    from app.engine import permissions
    cache = tmp_path / "_cacache"
    (cache / "content-v2").mkdir(parents=True)
    (cache / "content-v2" / "blob").write_text("x")
    (cache / "content-v2").chmod(0o555)
    try:
        st = os.lstat(cache / "content-v2")
        assert permissions.foreign_folder(str(cache / "content-v2"), st)
        assert "inside it belongs to" in permissions.inner_blocker(str(cache), {os.path.normcase(str(cache / "content-v2"))})
        assert permissions.inner_blocker(str(tmp_path / "other"), {str(cache / "content-v2")}) == ""
    finally:
        (cache / "content-v2").chmod(0o755)


@needs_user
def test_empty_erases_the_users_own_read_only_folders(tmp_path):
    from app.engine import reclaim
    item = tmp_path / "disc copy"
    (item / "sub").mkdir(parents=True)
    (item / "sub" / "track.wav").write_text("x")
    (item / "sub").chmod(0o555)
    reclaim._erase(str(item))
    assert not item.exists()


@needs_user
def test_restore_into_a_folder_you_cant_change_says_so(tmp_path, someone_else):
    from app.engine import recycle
    stored = tmp_path / "held.tmp"
    stored.write_text("x")
    target = tmp_path / "proj"
    target.mkdir()
    target.chmod(0o555)
    try:
        record = {"timestamp": 1, "paths": [str(target / "a.tmp")], "held": {str(target / "a.tmp"): str(stored)}}
        errors = recycle.restore_cleanup(record)["errors"]
        assert errors and errors[0]["error"].startswith("Can't put it back: ")
        assert "Errno" not in errors[0]["error"]
    finally:
        target.chmod(0o755)


def test_apt_hint_only_when_packages_are_there(tmp_path):
    from app.engine import linux
    archives = tmp_path / "var" / "cache" / "apt" / "archives"
    (archives / "partial").mkdir(parents=True)
    (archives / "lock").write_text("")
    (archives / "partial").chmod(0)
    try:
        assert not [h for h in linux.system_cleanup_hints(str(tmp_path)) if "APT" in h["name"]]
        (archives / "big.deb").write_bytes(b"d" * 2_000_000)
        assert [h for h in linux.system_cleanup_hints(str(tmp_path)) if "APT" in h["name"]][0]["size_bytes"] == 2_000_000
    finally:
        (archives / "partial").chmod(0o755)


def test_crontab_not_allowed_is_explained(monkeypatch, tmp_path, linux_os):
    denied = SimpleNamespace(returncode=1, stdout="",
                             stderr="You (bob) are not allowed to use this program (crontab)\n")
    monkeypatch.setattr(linux_scheduler, "_run", lambda args, **kw: denied if args[0] == "crontab"
                        else SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(linux_scheduler.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(linux_scheduler, "systemd_user_available", lambda: False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert not linux_scheduler.cron_available()
    assert not scheduler.scheduling_supported()
    assert "cron.allow" in scheduler.unsupported_reason()
    with pytest.raises(linux_scheduler.LinuxScheduleError, match="isn't allowed to use cron"):
        linux_scheduler.install("daily", "09:00", "MON", ["x"])
    linux_scheduler.remove()  # Nothing to remove, and no error
    # With a systemd timer the schedule still works, and the denied crontab isn't a problem
    monkeypatch.setattr(linux_scheduler, "systemd_user_available", lambda: True)
    assert linux_scheduler.install("daily", "09:00", "MON", ["x"]) == linux_scheduler.SYSTEMD


@needs_user
@pytest.mark.skipif(not __import__("sys").platform.startswith("linux"), reason="the freedesktop Trash")
def test_trash_from_a_read_only_folder_leaves_no_stray_note(tmp_path, monkeypatch, linux_os):
    from app.engine import cleaner
    import send2trash.plat_other as xdg
    trash = tmp_path / "share" / "Trash"
    trash.mkdir(parents=True)
    monkeypatch.setattr(xdg, "HOMETRASH", str(trash))  # send2trash reads XDG_DATA_HOME once, at import
    monkeypatch.setattr(xdg, "HOMETRASH_B", os.fsencode(str(trash)))
    folder = tmp_path / "ro"
    folder.mkdir()
    (folder / "a.tmp").write_text("x")
    folder.chmod(0o555)
    try:
        cleaner._send_to_trash(str(folder / "a.tmp"))
    finally:
        folder.chmod(0o755)
    info = sorted(p.name for p in (tmp_path / "share" / "Trash" / "info").iterdir())
    files = sorted(p.name for p in (tmp_path / "share" / "Trash" / "files").iterdir())
    assert info == [f + ".trashinfo" for f in files] == ["a.tmp.trashinfo"]
