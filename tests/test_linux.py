"""Linux support (MX Linux and other freedesktop.org desktops): protected folders, junk locations,
disks and the Trash, the folder picker, the file manager and scheduled scans with systemd or cron.

These run on every system: Linux behavior is switched on with the `linux` fixture. Tests that
need real POSIX paths skip on Windows."""
import os
import time
from types import SimpleNamespace

import pytest

from app import main
from app.config import ScanOptions
from app.engine import linux, linux_scheduler, locations, osinfo, recycle, scheduler, storage
from app.engine.classifier import is_system_protected_path
from app.engine.leftovers import find_program_leftovers
from app.engine.locations import (
    downloads_folder, is_protected_user_folder, junk_locations, protected_user_folders, system_cleanup_hints,
)
from app.engine.scanner import FastScanner
from tests.helpers import api_client

posix_only = pytest.mark.skipif(os.name == "nt", reason="needs POSIX paths")


@pytest.fixture
def linux_os(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(osinfo, "is_linux", lambda: True)


@pytest.fixture
def linux_home(tmp_path, monkeypatch, linux_os):
    """A fake Linux home folder with the usual hidden folders and user folders."""
    if os.name == "nt":
        pytest.skip("Linux home-folder rules need a POSIX temp path")
    home = tmp_path / "home" / "alex"
    for rel in [".cache/thumbnails/normal", ".cache/google-chrome/Default/Cache", ".cache/mozilla/firefox/x.default",
                ".cache/sessions", ".cache/spotify/Storage", ".config/xfce4", ".local/share/Trash/files",
                ".mozilla/firefox/x.default", ".ssh", ".npm/_cacache", ".npm/_logs",
                "Desktop", "Documents", "Bilder", "Téléchargements", "Projects/app"]:
        (home / rel).mkdir(parents=True)
    (home / ".config" / "user-dirs.dirs").write_text(
        '# written by xdg-user-dirs-update\n'
        'XDG_DESKTOP_DIR="$HOME/Desktop"\n'
        'XDG_DOWNLOAD_DIR="$HOME/Téléchargements"\n'
        'XDG_PICTURES_DIR="$HOME/Bilder"\n'
        'XDG_TEMPLATES_DIR="$HOME/"\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return home


# ---------------------------------------------------------------- System and hidden folders

@pytest.mark.parametrize("path", [
    "/usr/lib/x.so", "/etc/fstab", "/boot/vmlinuz", "/var/log/syslog.1", "/var/cache/apt/archives/a.deb",
    "/opt/app/cache/x.tmp", "/proc/1/status", "/root/.config/x.log", "/run/user/1000/x", "/snap/core/1",
    "/home/alex/.config/xfce4/x.log", "/home/alex/.ssh/id_rsa", "/home/alex/.mozilla/firefox/cache2/x",
    "/home/alex/.local/share/Trash/files/x", "/home/alex/.bash_history", "/home/alex/.npm/_logs/x.log",
    "/media/alex/USB/.Trash-1000/files/x.tmp", "/home/snapshot/snapshot-20261019.iso", "/timeshift/snapshots/x",
])
def test_system_and_settings_folders_are_protected(linux_os, path):
    assert is_system_protected_path(path)


@pytest.mark.parametrize("path", [
    "/home/alex/Downloads/setup.deb", "/home/alex/.cache/thumbnails/normal/x.png", "/home/alex/.cache",
    "/home/alex/.npm", "/home/alex/.npm/_cacache/index", "/media/alex/USB/old.tmp", "/mnt/data/x.iso",
    "/run/media/alex/USB/x.tmp", "/var/tmp/x.tmp", "/root/Downloads/x.deb", "/tmp/x.tmp", "/home/alex/Projects/app/node_modules",
])
def test_user_files_and_caches_are_not_protected(linux_os, path):
    assert not is_system_protected_path(path)


def test_linux_rules_only_apply_on_linux(monkeypatch):
    monkeypatch.setattr(osinfo, "is_linux", lambda: False)
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    assert not is_system_protected_path("/home/alex/.config/x.log")


def test_xdg_user_dirs_follow_user_dirs_file(linux_home):
    dirs = linux.xdg_user_dirs()
    assert dirs["DOWNLOAD"] == str(linux_home / "Téléchargements")
    assert dirs["PICTURES"] == str(linux_home / "Bilder")
    assert dirs["DOCUMENTS"] == str(linux_home / "Documents")  # not in the file: the usual name
    assert "TEMPLATES" not in dirs  # set to the home folder itself: not used


def test_user_folders_and_app_data_folders_are_never_deleted_whole(linux_home):
    folders = protected_user_folders()
    for rel in ["Bilder", "Téléchargements", "Documents", "Desktop", ".cache", ".config", ".local/share", ".npm"]:
        assert is_protected_user_folder(str(linux_home / rel), folders=folders), rel
    assert not is_protected_user_folder(str(linux_home / "Projects" / "app"), folders=folders)
    assert is_protected_user_folder(str(linux_home.parent / "other-user"))


def test_downloads_folder_is_the_localized_one(linux_home):
    assert downloads_folder() == str(linux_home / "Téléchargements")


# ---------------------------------------------------------------- Junk locations

def test_junk_locations_are_known_cache_folders_only(linux_home):
    found = {loc.path: loc for loc in junk_locations()}
    cache = linux_home / ".cache"
    assert str(cache / "thumbnails") in found
    assert str(cache / "google-chrome") in found
    assert str(cache / "mozilla") in found
    assert str(linux_home / ".npm" / "_cacache") in found
    # Xfce's saved sessions and Spotify's offline music live in ~/.cache too: not listed
    assert str(cache / "sessions") not in found and str(cache / "spotify") not in found
    assert all(loc.whole_dir for loc in found.values())


def test_junk_locations_use_xdg_cache_home(linux_home, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    (other / "pip").mkdir(parents=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(other))
    assert [loc.path for loc in junk_locations()] == [str(other / "pip"), str(linux_home / ".npm" / "_cacache")]


@posix_only
def test_scanning_home_lists_cache_junk_but_never_settings(linux_home):
    old = time.time() - 30 * 86400
    for rel in [".config/app/logs/run.log", ".local/share/app/cache/x.tmp", ".cache/app/x.tmp",
                ".cache/thumbnails/normal/a.png", "Projects/app/build.tmp"]:
        path = linux_home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
        os.utime(path, (old, old))
    options = ScanOptions(target_path=str(linux_home), junk_locations=junk_locations(), scan_junk_locations=True,
                          min_file_size_bytes=0)
    paths = {item.path for item in FastScanner(options).run_scan()}
    assert str(linux_home / ".cache" / "app" / "x.tmp") in paths
    assert str(linux_home / ".cache" / "thumbnails") in paths
    assert not any("/.config/" in p or "/.local/" in p or "/.ssh" in p for p in paths)


def test_no_program_leftovers_on_linux(linux_os):
    assert find_program_leftovers() == []


@posix_only
def test_system_cleanup_hints_show_apt_cache_with_its_command(tmp_path):
    archives = tmp_path / "var" / "cache" / "apt" / "archives"
    archives.mkdir(parents=True)
    (archives / "big_1.0_amd64.deb").write_bytes(b"x" * (2 * 1024 * 1024))
    (archives / "lock").write_bytes(b"x" * (3 * 1024 * 1024))  # not a package: not counted
    hints = linux.system_cleanup_hints(root=str(tmp_path))
    assert hints == [{"name": "Downloaded software packages (APT cache)", "path": str(archives),
                      "size_bytes": 2 * 1024 * 1024, "fix": "sudo apt clean"}]
    (archives / "big_1.0_amd64.deb").write_bytes(b"x" * 10)  # under 1 MB: not worth showing
    assert linux.system_cleanup_hints(root=str(tmp_path)) == []


def test_cleanup_hints_on_linux_come_from_the_linux_list(linux_os, monkeypatch):
    monkeypatch.setattr(linux, "system_cleanup_hints", lambda size_limit: [{"name": "x", "path": "/var/x"}])
    assert system_cleanup_hints() == [{"name": "x", "path": "/var/x"}]


# ---------------------------------------------------------------- Disks and the Trash

def test_mount_table_unescapes_spaces():
    table = linux.parse_mounts("/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /media/alex/My\\040Stick vfat rw 0 0\n"
                               "server:/x /mnt/nas nfs4 rw 0 0\n")
    assert table == {"/": "ext4", "/media/alex/My Stick": "vfat", "/mnt/nas": "nfs4"}


@posix_only
def test_trash_is_used_on_home_disk_and_linux_disks_only(monkeypatch, tmp_path):
    monkeypatch.setattr(linux, "_same_disk", lambda a, b: False)
    assert not linux.has_trash("/mnt/nas/x", "/mnt/nas", "nfs4")
    assert not linux.has_trash("/media/alex/USB/x", "/media/alex/USB", "ext4")
    assert not linux.has_trash("/run/media/alex/USB/x", "/run/media/alex/USB", "vfat")
    assert not linux.has_trash("/mnt/windows/x", "/mnt/windows", "fuseblk")
    monkeypatch.setattr(linux, "trash_usable", lambda root: True)
    assert linux.has_trash("/data/x", "/data", "ext4")
    # A disk whose top folder only root can change can't hold the user's Trash
    monkeypatch.setattr(linux, "trash_usable", lambda root: False)
    assert not linux.has_trash("/data/x", "/data", "ext4")
    assert "belongs to another account" in linux.holding_reason("/data", "ext4", "ext4")
    monkeypatch.setattr(linux, "_same_disk", lambda a, b: True)
    assert linux.has_trash(str(tmp_path / "x"), "/", "ext4")


@posix_only
def test_disk_info_says_why_usb_drives_use_the_holding_folder(linux_os, monkeypatch):
    monkeypatch.setattr(recycle, "mount_point", lambda path: "/media/alex/USB")
    monkeypatch.setattr(recycle, "is_read_only", lambda path: False)
    monkeypatch.setattr(linux, "mounts", lambda: {"/media/alex/USB": "vfat"})
    monkeypatch.setattr(linux, "_same_disk", lambda a, b: False)
    info = recycle.disk_info("/media/alex/USB/old.tmp")
    assert info["destination"] == recycle.DEST_HOLDING
    assert info["fs_label"] == "FAT" and info["name"] == "USB"
    assert "USB drives" in info["reason"]
    monkeypatch.setattr(linux, "mounts", lambda: {"/media/alex/USB": "cifs"})
    assert recycle.disk_info("/media/alex/USB/old.tmp")["reason"] == "network drives have no Trash"


def test_drives_are_home_root_and_mounted_disks(tmp_path, monkeypatch):
    for rel in ["home", "media/alex/USB", "mnt/data", "run/media/alex/Card"]:
        (tmp_path / rel).mkdir(parents=True)
    table = {"/": "ext4", "/boot/efi": "vfat", "/media/alex/USB": "vfat", "/mnt/data": "ext4",
             "/run/media/alex/Card": "exfat", "/run/user/1000": "tmpfs", "/media/alex/net": "autofs"}
    monkeypatch.setattr(linux.os.path, "isdir", lambda p: True)
    found = linux.drives(lambda path, label: {"drive": path, "label": label},
                         env={"HOME": "/home/alex"}, mount_table=table)
    assert [(d["drive"], d["label"]) for d in found] == [
        ("/home/alex", "Home"), ("/", "File System"), ("/media/alex/USB", "USB"), ("/mnt/data", "data"),
        ("/run/media/alex/Card", "Card")]


@posix_only
def test_deleted_item_is_restored_from_the_freedesktop_trash(linux_home, tmp_path, monkeypatch):
    """What send2trash writes (files/ and info/*.trashinfo) is what Restore reads back."""
    trash = linux_home / ".local" / "share" / "Trash"
    (trash / "info").mkdir(parents=True)
    original = linux_home / "Projects" / "app" / "old.tmp"
    original.write_text("junk")
    started = time.time()
    os.rename(original, trash / "files" / "old.tmp")
    (trash / "info" / "old.tmp.trashinfo").write_text(
        "[Trash Info]\nPath=" + str(original).replace(" ", "%20") + "\nDeletionDate="
        + time.strftime("%Y-%m-%dT%H:%M:%S") + "\n", encoding="utf-8")
    result = recycle.restore_cleanup({"paths": [str(original)], "started_at": started, "timestamp": time.time()})
    assert result["restored"] == [str(original)] and original.read_text() == "junk"
    assert not (trash / "info" / "old.tmp.trashinfo").exists()


# ---------------------------------------------------------------- Folder picker, file manager, Trash

def _which(*installed):
    return lambda name: f"/usr/bin/{name}" if name in installed else None


def test_folder_pickers_in_order(tmp_path):
    commands = linux.folder_picker_commands(str(tmp_path), _which("zenity", "kdialog", "yad"))
    assert [c[0] for c in commands] == ["zenity", "kdialog", "yad"]
    assert commands[0][-1] == "--filename=" + str(tmp_path).rstrip("/") + "/"
    assert commands[1][-1] == str(tmp_path)
    assert linux.folder_picker_commands("", _which()) == []


def test_choose_folder_returns_the_choice_or_nothing_when_cancelled(tmp_path):
    def run(answer, code=0):
        return lambda cmd, **kw: SimpleNamespace(returncode=code, stdout=answer, stderr="")
    assert linux.choose_folder("", run(str(tmp_path) + "/\n"), _which("zenity")) == (str(tmp_path), None)
    assert linux.choose_folder("", run(str(tmp_path) + "|\n"), _which("yad")) == (str(tmp_path), None)
    assert linux.choose_folder("", run("", 1), _which("zenity")) == ("", None)
    assert linux.choose_folder("", run(""), _which()) == ("", linux.NO_PICKER_ERROR)


def test_choose_folder_tries_the_next_picker_when_one_fails(tmp_path):
    tried = []

    def run(cmd, **kw):
        tried.append(cmd[0])
        if cmd[0] == "zenity":
            return SimpleNamespace(returncode=255, stdout="", stderr="cannot open display")
        return SimpleNamespace(returncode=0, stdout=str(tmp_path) + "\n", stderr="")
    assert linux.choose_folder("", run, _which("zenity", "kdialog")) == (str(tmp_path), None)
    assert tried == ["zenity", "kdialog"]


def test_browse_folder_api_uses_the_linux_picker(linux_os, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "gui_window", None)
    monkeypatch.setattr(linux, "choose_folder", lambda start: (str(tmp_path), None))
    assert api_client().post("/api/system/browse-folder").json() == {"path": str(tmp_path)}
    monkeypatch.setattr(linux, "choose_folder", lambda start: ("", linux.NO_PICKER_ERROR))
    assert api_client().post("/api/system/browse-folder").json() == {"path": "", "error": linux.NO_PICKER_ERROR}


@posix_only
def test_reveal_selects_a_file_then_falls_back_to_its_folder():
    commands = linux.reveal_commands("/home/alex/My File.txt", True, _which("dbus-send"))
    assert commands[0][0] == "dbus-send" and "array:string:file:///home/alex/My%20File.txt" in commands[0]
    assert commands[1] == ["xdg-open", "/home/alex"]
    assert linux.reveal_commands("/home/alex/Folder", False, _which("dbus-send")) == [["xdg-open", "/home/alex/Folder"]]

    opened = []
    linux.reveal("/home/alex/x.txt", True, run=lambda cmd, **kw: SimpleNamespace(returncode=1),
                 popen=lambda cmd, **kw: opened.append(cmd))
    assert opened == [["xdg-open", "/home/alex"]]


def test_open_trash_prefers_gio(monkeypatch):
    assert linux.open_trash_command(_which("gio")) == ["gio", "open", "trash:///"]
    assert linux.open_trash_command(_which())[0] == "xdg-open"


def test_open_recycle_bin_api_opens_the_trash(linux_os, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    started = []
    monkeypatch.setattr(main.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    assert api_client().post("/api/system/open-recycle-bin").json() == {"success": True}
    assert started == [linux.open_trash_command()]


def test_packaged_app_gives_tools_the_system_libraries(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/junkzero/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    assert linux.subprocess_env()["LD_LIBRARY_PATH"] == "/usr/local/lib"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    monkeypatch.setattr(linux.sys, "frozen", True, raising=False)
    assert "LD_LIBRARY_PATH" not in linux.subprocess_env()


def test_system_info_says_linux(linux_os, monkeypatch):
    monkeypatch.setattr(linux_scheduler, "scheduler_kind", lambda: linux_scheduler.CRON)
    info = api_client().get("/api/system/info").json()
    assert info["platform"] == "linux" and info["trash"] == "Trash"
    assert info["scheduler"] == "cron" and info["scheduling_supported"] is True


def test_second_launch_opens_the_browser_again(monkeypatch):
    opened = []
    monkeypatch.setattr(main, "gui_window", None)
    monkeypatch.setattr(main, "browser_url", "http://127.0.0.1:8000/#token=x")
    monkeypatch.setattr(main.webbrowser, "open", opened.append)
    assert api_client().post("/api/instance/focus").json() == {"focused": True}
    assert opened == ["http://127.0.0.1:8000/#token=x"]


# ---------------------------------------------------------------- Scheduled scans

def test_systemd_units_run_the_report_and_catch_up():
    service = linux_scheduler.systemd_service(["/opt/junk zero/JunkZero", "--mode", "report", "100%"])
    assert 'ExecStart="/opt/junk zero/JunkZero" "--mode" "report" "100%%"' in service
    assert linux_scheduler.parse_exec_start(service) == ["/opt/junk zero/JunkZero", "--mode", "report", "100%"]
    weekly = linux_scheduler.systemd_timer("weekly", "07:15", "SUN")
    assert "OnCalendar=Sun *-*-* 07:15:00" in weekly and "Persistent=true" in weekly
    assert "OnCalendar=*-*-* 09:00:00" in linux_scheduler.systemd_timer("daily", "09:00", "MON")


def test_cron_line_uses_cron_weekdays_and_quotes_the_command():
    line = linux_scheduler.cron_line("weekly", "07:05", "SUN", ["/usr/bin/python3", "/home/a b/scan.py"])
    assert line == "5 7 * * 0 /usr/bin/python3 '/home/a b/scan.py' " + linux_scheduler.CRON_MARKER
    assert linux_scheduler.cron_line("daily", "21:30", "MON", ["x"]).startswith("30 21 * * * x ")
    assert linux_scheduler.cron_line("weekly", "00:00", "MON", ["x"]).startswith("0 0 * * 1 ")


class FakeCrontab:
    def __init__(self, text=None):
        self.text = text
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(args)
        if args[:2] == ["crontab", "-l"]:
            if self.text is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="no crontab for alex")
            return SimpleNamespace(returncode=0, stdout=self.text, stderr="")
        if args[:2] == ["crontab", "-"]:
            self.text = kw["input"]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="no systemd here")


@pytest.fixture
def cron_only(monkeypatch, tmp_path, linux_os):
    fake = FakeCrontab("MAILTO=alex\n0 3 * * * backup.sh\n")
    monkeypatch.setattr(linux_scheduler, "_run", fake)
    monkeypatch.setattr(linux_scheduler, "systemd_user_available", lambda: False)
    monkeypatch.setattr(linux_scheduler, "cron_available", lambda: True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return fake


def test_schedule_with_cron_keeps_the_users_own_lines(cron_only, tmp_path):
    schedule = scheduler.save_schedule("weekly", "07:15", "SUN", [str(tmp_path)], False)
    assert schedule["enabled"]
    lines = cron_only.text.splitlines()
    assert lines[:2] == ["MAILTO=alex", "0 3 * * * backup.sh"]
    assert lines[2].startswith("15 7 * * 0 ") and lines[2].endswith(linux_scheduler.CRON_MARKER)
    script = storage.data_dir() / "scheduled_scan.py"
    assert linux_scheduler.current_command() == scheduler.launchd_program_args(str(script))
    compile(script.read_text(), str(script), "exec")

    scheduler.save_schedule("daily", "09:00", "MON", [str(tmp_path)], False)
    assert sum(linux_scheduler.CRON_MARKER in line for line in cron_only.text.splitlines()) == 1

    scheduler.delete_schedule()
    assert cron_only.text.splitlines() == ["MAILTO=alex", "0 3 * * * backup.sh"]
    assert scheduler.get_schedule()["enabled"] is False


def test_schedule_with_cron_when_user_has_no_crontab(cron_only, tmp_path):
    cron_only.text = None
    scheduler.save_schedule("daily", "09:00", "MON", [str(tmp_path)], False)
    assert len(cron_only.text.splitlines()) == 1


def test_startup_points_the_schedule_at_this_copy(cron_only, tmp_path, monkeypatch):
    storage.update_settings(schedule={"enabled": True, "frequency": "daily", "time": "09:00", "day": "MON"})
    cron_only.text = "0 9 * * * /old/place/JunkZero --mode report " + linux_scheduler.CRON_MARKER + "\n"
    monkeypatch.setattr(scheduler.sys, "frozen", True, raising=False)
    monkeypatch.setattr(scheduler.sys, "executable", "/opt/junkzero/JunkZero")
    assert scheduler.ensure_task_current() is True
    assert linux_scheduler.current_command() == ["/opt/junkzero/JunkZero", "--mode", "report"]
    assert scheduler.ensure_task_current() is False


def test_schedule_with_systemd_writes_a_user_timer(monkeypatch, tmp_path, linux_os):
    calls = []
    monkeypatch.setattr(linux_scheduler, "_run", lambda args, **kw: calls.append(args) or SimpleNamespace(
        returncode=0 if args[0] == "systemctl" else 1, stdout="", stderr="no crontab for alex"))
    monkeypatch.setattr(linux_scheduler, "systemd_user_available", lambda: True)
    monkeypatch.setattr(linux_scheduler, "cron_available", lambda: True)
    monkeypatch.setattr(linux_scheduler.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    scheduler.save_schedule("weekly", "07:15", "MON", [str(tmp_path)], False)
    units = tmp_path / "config" / "systemd" / "user"
    assert "OnCalendar=Mon *-*-* 07:15:00" in (units / "junkzero-scan.timer").read_text()
    assert ["systemctl", "--user", "enable", "--now", "junkzero-scan.timer"] in calls
    script = storage.data_dir() / "scheduled_scan.py"
    assert linux_scheduler.current_command() == scheduler.launchd_program_args(str(script))

    scheduler.delete_schedule()
    assert not (units / "junkzero-scan.timer").exists() and not (units / "junkzero-scan.service").exists()
    assert ["systemctl", "--user", "disable", "--now", "junkzero-scan.timer"] in calls


def test_no_scheduler_means_scheduling_is_off(linux_os, monkeypatch):
    monkeypatch.setattr(linux_scheduler, "systemd_user_available", lambda: False)
    monkeypatch.setattr(linux_scheduler, "cron_available", lambda: False)
    assert scheduler.scheduling_supported() is False
    assert osinfo.platform_terms()["scheduling_supported"] is False
    with pytest.raises(scheduler.ScheduleError, match="systemd or cron"):
        scheduler.save_schedule("daily", "09:00", "MON", [], True)


def test_validation_wording_has_no_windows_on_linux(linux_os):
    with pytest.raises(scheduler.ScheduleError, match="^Choose at least one folder or include the junk locations$"):
        scheduler.validate_schedule("daily", "09:00", "MON", [], False)


def test_junk_locations_dispatch(linux_home):
    assert locations.junk_locations() == locations.linux_junk_locations()


def test_no_windows_app_caches_on_linux(linux_os, tmp_path):
    from app.engine import appcaches
    (tmp_path / "Slack" / "Cache").mkdir(parents=True)
    assert appcaches.app_cache_entries({"APPDATA": str(tmp_path)}, running=set()) == []


# ---------------------------------------------------------------- Disks whose top folder belongs to root

@posix_only
def test_trash_usable_needs_the_users_trash_or_a_writable_top_folder(tmp_path, monkeypatch):
    disk = tmp_path / "disk"
    disk.mkdir()
    monkeypatch.setattr(linux.os, "access", lambda p, mode: str(p) != str(disk))
    assert not linux.trash_usable(str(disk))
    (disk / f".Trash-{os.getuid()}").mkdir()
    assert linux.trash_usable(str(disk))


def _root_owned_usb(tmp_path, monkeypatch):
    """An ext4 USB disk (as MX Linux mounts it under /media) whose top folder belongs to root,
    with the user's own old project inside, like a backup copied from another computer."""
    disk = tmp_path / "files"
    target = disk / "EXT4" / "Red" / "Old-HD" / "app" / "target"
    (target / "classes").mkdir(parents=True)
    (target / "classes" / "Main.class").write_text("x")
    monkeypatch.setattr(recycle, "has_recycle_bin", lambda p: False)
    monkeypatch.setattr(recycle, "mount_point", lambda p: str(disk))
    monkeypatch.setattr(recycle, "_writable_dir", lambda f: os.path.isdir(f) and str(f) != str(disk))
    return disk, target


@posix_only
def test_items_on_a_disk_with_a_root_owned_top_folder_are_set_aside_and_restored(linux_os, tmp_path, monkeypatch):
    from app.engine import cleaner
    disk, target = _root_owned_usb(tmp_path, monkeypatch)
    assert recycle.holding_root(str(target)) == str(disk / "EXT4")
    result = cleaner.delete_items([{"path": str(target), "size_bytes": 1, "is_directory": True}], exclusions=[])
    assert result.errors == [] and result.deleted_count == 1 and not target.exists()
    held = result.held[str(target)]
    assert held.startswith(str(disk / "EXT4" / ".JunkZero-holding") + os.sep)
    assert not (disk / ".JunkZero-holding").exists()
    outcome = recycle.restore_cleanup({"timestamp": time.time(), "mode": "recycle_bin", "paths": [str(target)],
                                       "held": result.held, "trash_ids": {}, "restored": []})
    assert outcome["errors"] == [] and (target / "classes" / "Main.class").read_text() == "x"


@posix_only
def test_holding_root_is_the_disk_top_when_the_user_can_change_it(tmp_path, monkeypatch):
    disk, target = _root_owned_usb(tmp_path, monkeypatch)
    monkeypatch.setattr(recycle, "_writable_dir", lambda f: True)
    assert recycle.holding_root(str(target)) == str(disk)


@posix_only
def test_removal_blocker_names_items_of_another_account(tmp_path, monkeypatch):
    from app.engine import permissions
    folder = tmp_path / "theirs"
    folder.mkdir()
    item = folder / "old.tmp"
    item.write_text("x")
    owner = folder.stat().st_uid
    real_access = os.access
    monkeypatch.setattr(permissions.os, "getuid", lambda: owner + 1)
    monkeypatch.setattr(permissions.os, "access", lambda p, mode: False if str(p) == str(folder) else real_access(p, mode))
    assert "the folder it is in belongs to" in permissions.removal_blocker(str(item))
    # The user's own read-only folder is no blocker: JunkZero makes it writable before deleting
    monkeypatch.setattr(permissions.os, "getuid", lambda: owner)
    assert permissions.removal_blocker(str(item)) == ""


@posix_only
def test_scan_leaves_out_junk_the_user_cannot_remove(linux_home, tmp_path, monkeypatch):
    from app.engine import permissions
    project = linux_home / "Projects" / "app"
    (project / "old.tmp").write_text("x" * 10)
    blocked = linux_home / "Projects" / "app" / "__pycache__"
    blocked.mkdir()
    (blocked / "m.pyc").write_text("x")
    monkeypatch.setattr(permissions, "removal_blocker",
                        lambda p: "it belongs to the system" if os.path.basename(p) == "__pycache__" else "")
    scanner = FastScanner(ScanOptions(target_path=str(linux_home / "Projects")))
    items = scanner.run_scan()
    assert not any(i.name == "__pycache__" for i in items)
    assert scanner.stats.not_allowed_items == 1
    assert scanner.stats.not_allowed_examples[0].endswith("(it belongs to the system)")
