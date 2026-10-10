"""macOS support: system protection, junk locations, leftovers, drives, Finder and launchd.

These run on every system: macOS behavior is switched on with the `mac` fixture."""
import os
import plistlib
import sys
import time

import pytest

from app.config import CAT_LEFTOVERS, CAT_TEMP_JUNK, ScanOptions
from app.engine import cleaner, osinfo, scanner as scanner_mod, scheduler
from app.engine.classifier import is_system_protected_path
from app.engine.leftovers import find_leftovers, mac_installed_programs
from app.engine.locations import is_protected_user_folder, junk_locations, mac_junk_locations
from app.engine.scanner import FastScanner, get_mac_drives

DAY = 86400


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)


@pytest.fixture
def mac_home(tmp_path, monkeypatch, mac):
    """A fake macOS home folder with the usual Library layout."""
    home = tmp_path / "home"
    lib = home / "Library"
    for rel in ["Caches/com.spotify.client", "Caches/Homebrew/downloads", "Caches/com.apple.Safari",
                "Logs/DiagnosticReports", "Developer/Xcode/DerivedData/MyApp-abc",
                "Developer/Xcode/Archives", "Mail/V10", "Application Support/MobileSync/Backup",
                "Application Support/OldGame", "Application Support/Slack"]:
        (lib / rel).mkdir(parents=True)
    for name in ["Desktop", "Documents", "Downloads", "Movies", "Pictures", ".Trash"]:
        (home / name).mkdir()
    temp = tmp_path / "T"
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.delenv("USERPROFILE", raising=False)
    return home


# ------------------------------------------------------------- System protection

@pytest.mark.parametrize("path, protected", [
    ("/System/Library/CoreServices/Finder.app", True),
    ("/Applications/Safari.app", True),
    ("/Library/Preferences/x.plist", True),
    ("/usr/local/bin/brew", True),
    ("/private/etc/hosts", True),
    ("/private/var/db/x.log", True),
    ("/var/folders/ab/cd/T/setup.tmp", False),
    ("/private/var/folders/ab/cd/T/setup.tmp", False),
    ("/private/tmp/x.tmp", False),
    ("/Users/alex/Downloads/setup.dmg", False),
    ("/Users/alex/Library", False),
    ("/Users/alex/Library/Mail/V10/x.log", True),
    ("/Users/alex/Library/Containers/com.foo/Data/x.tmp", True),
    ("/Users/alex/Library/Mobile Documents/com~apple~CloudDocs/a.tmp", True),
    ("/Users/alex/Library/Caches/com.spotify.client", False),
    ("/Users/alex/Library/Caches/com.apple.Safari", True),
    ("/Users/alex/Library/Logs/DiagnosticReports/a.crash", False),
    ("/Users/alex/Library/Application Support/OldGame", False),
    ("/Users/alex/Library/Application Support/MobileSync/Backup", True),
    ("/Users/alex/Library/Developer", False),
    ("/Users/alex/Library/Developer/Xcode/DerivedData/MyApp-abc", False),
    ("/Users/alex/Library/Developer/Xcode/Archives/2024/a.xcarchive", True),
    ("/Users/alex/.Trash/old.dmg", True),
    ("/Volumes/USB/.Spotlight-V100/Store", True),
    ("/Volumes/USB/old/setup.tmp", False),
    ("/Users/alex/Downloads/Old.app", False),
    ("/Users/alex/Downloads/Old.app/Contents/MacOS/Old", True),
    ("/Users/alex/Pictures/Photos Library.photoslibrary/originals/a.jpg", True),
])
def test_macos_system_and_library_protection(mac, path, protected):
    assert is_system_protected_path(path) is protected


def test_macos_rules_leave_windows_paths_and_other_systems_alone(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    assert not is_system_protected_path("/Users/alex/Library/Mail/x.log")
    assert not is_system_protected_path("/Applications/Foo.app/Contents/x")
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)
    assert not is_system_protected_path("C:\\Users\\alex\\Library\\Mail\\x.log")
    assert is_system_protected_path("C:\\Windows\\Temp\\x.tmp")


def test_library_in_the_current_home_folder_is_protected_wherever_home_is(mac_home):
    assert is_system_protected_path(str(mac_home / "Library" / "Mail" / "V10"))
    assert not is_system_protected_path(str(mac_home / "Library" / "Caches" / "com.spotify.client"))


# ------------------------------------------------------------- Junk locations

def test_mac_junk_locations(mac_home, tmp_path):
    locations = {loc.id: loc for loc in mac_junk_locations()}
    assert set(locations) == {
        "user_temp", "mac_logs", "mac_cache:com.spotify.client", "mac_cache:homebrew", "xcode_derived_data",
    }
    assert locations["mac_cache:homebrew"].label == "App cache (Homebrew downloads)"
    assert locations["mac_cache:homebrew"].whole_dir
    assert not locations["mac_logs"].whole_dir
    assert [loc.id for loc in junk_locations()] == [loc.id for loc in mac_junk_locations()]
    assert mac_junk_locations({}) == []


def test_quick_clean_on_mac_flags_caches_logs_and_temp(mac_home, tmp_path):
    lib = mac_home / "Library"
    old = time.time() - 3 * DAY
    for f in [lib / "Logs" / "DiagnosticReports" / "App.crash", tmp_path / "T" / "setup.dat",
              lib / "Caches" / "com.spotify.client" / "data.bin", lib / "Caches" / "com.apple.Safari" / "c.db"]:
        f.write_text("x" * 10)
        os.utime(f, (old, old))
    items = FastScanner(ScanOptions(target_path="", junk_locations=mac_junk_locations(),
                                    scan_junk_locations=True)).run_scan()
    paths = {i.path: i for i in items}
    assert str(lib / "Logs" / "DiagnosticReports" / "App.crash") in paths
    assert str(tmp_path / "T" / "setup.dat") in paths
    assert paths[str(lib / "Caches" / "com.spotify.client")].category == CAT_TEMP_JUNK
    assert not any("com.apple.Safari" in p for p in paths)


def test_home_scan_on_mac_skips_private_library_folders_and_trash(mac_home):
    old = time.time() - 3 * DAY
    for f in [mac_home / "Library" / "Mail" / "V10" / "index.log", mac_home / ".Trash" / "old.tmp",
              mac_home / "Downloads" / "left.tmp"]:
        f.write_text("x")
        os.utime(f, (old, old))
    items = FastScanner(ScanOptions(target_path=str(mac_home), include_empty_folders=False)).run_scan()
    names = {i.name for i in items}
    assert "left.tmp" in names
    assert "index.log" not in names and "old.tmp" not in names


# ------------------------------------------------------------- Personal folders

def test_mac_personal_folders_are_protected(mac_home):
    for rel in ["", "Library", "Movies", "Documents", "Library/Caches", "Library/Application Support"]:
        assert is_protected_user_folder(str(mac_home / rel) if rel else str(mac_home)), rel
    # Case differences don't get around it (macOS disks ignore case)
    assert is_protected_user_folder(str(mac_home / "documents"))
    assert not is_protected_user_folder(str(mac_home / "Library" / "Caches" / "com.spotify.client"))


def test_cleaner_blocks_mac_system_and_personal_folders(mac_home):
    result = cleaner.delete_items([
        {"path": str(mac_home / "Library"), "size_bytes": 0},
        {"path": str(mac_home / "Library" / "Mail"), "size_bytes": 0},
        {"path": "/System/Library", "size_bytes": 0},
    ], permanent=True)
    assert result.deleted_count == 0
    assert (mac_home / "Library" / "Mail").is_dir()


# ------------------------------------------------------------- Program leftovers

def _fake_mac_root(tmp_path, apps=12):
    root = tmp_path / "root"
    for i in range(apps):
        app = root / "Applications" / f"App{i:02d}.app" / "Contents"
        app.mkdir(parents=True)
        with open(app / "Info.plist", "wb") as f:
            plistlib.dump({"CFBundleIdentifier": f"com.vendor{i}.app{i}", "CFBundleName": f"App{i:02d}"}, f)
    slack = root / "Applications" / "Slack.app" / "Contents"
    slack.mkdir(parents=True)
    with open(slack / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.tinyspeck.slackmacgap", "CFBundleName": "Slack"}, f)
    (root / "System" / "Library" / "LaunchAgents").mkdir(parents=True)
    (root / "System" / "Library" / "LaunchAgents" / "com.apple.homeenergyd.plist").write_bytes(b"")
    (root / "opt" / "homebrew" / "Cellar" / "ffmpeg").mkdir(parents=True)
    return root


def test_mac_installed_programs_reads_apps_services_and_homebrew(tmp_path):
    installed = mac_installed_programs(home="", root=str(_fake_mac_root(tmp_path)))
    for folder in ["Slack", "com.tinyspeck.slackmacgap", "App03", "com.vendor3.app3", "homeenergyd", "ffmpeg"]:
        assert installed.matches(folder), folder
    assert not installed.matches("OldGame")


def test_mac_installed_programs_is_empty_when_apps_cannot_be_read(tmp_path):
    assert not mac_installed_programs(home="", root=str(_fake_mac_root(tmp_path, apps=3)))
    assert not mac_installed_programs(home="", root=str(tmp_path / "missing"))


def test_mac_leftovers_skip_apple_and_shared_folders(mac_home, tmp_path):
    support = mac_home / "Library" / "Application Support"
    for name in ["OldGame", "Slack", "com.apple.TCC", "CloudDocs", "MobileSync"]:
        (support / name).mkdir(exist_ok=True)
    now = time.time()
    for folder in support.iterdir():
        os.utime(folder, (now - 400 * DAY, now - 400 * DAY))
    installed = mac_installed_programs(home="", root=str(_fake_mac_root(tmp_path)))
    found = find_leftovers([str(support)], installed, now=now, mac=True)
    assert [l.name for l in found] == ["OldGame"]


def test_scanner_uses_mac_leftovers_on_mac(mac, monkeypatch, tmp_path):
    from app.engine import leftovers
    monkeypatch.setattr(leftovers, "find_mac_leftovers",
                        lambda: [leftovers.Leftover(str(tmp_path), tmp_path.name, time.time() - 400 * DAY, 400)])
    monkeypatch.setattr(leftovers, "find_windows_leftovers", lambda: pytest.fail("Windows leftovers used on a Mac"))
    items = FastScanner(ScanOptions(target_path="", include_leftovers=True)).run_scan()
    assert [i.category for i in items] == [CAT_LEFTOVERS]


# ------------------------------------------------------------- Drives, Finder, Trash, data folder

@pytest.mark.skipif(os.name == "nt", reason="'/' is the current drive on Windows, which may not hold the temp folder")
def test_mac_drives_list_startup_disk_and_external_volumes(tmp_path):
    volumes = tmp_path / "Volumes"
    (volumes / "Backup").mkdir(parents=True)
    (volumes / ".hidden").mkdir()
    drives = get_mac_drives(str(volumes))
    assert drives[0]["drive"] == "/" and drives[0]["label"] == "Macintosh HD"
    # tmp_path is on the startup disk, so "Backup" is skipped like the "Macintosh HD" link
    assert [d["drive"] for d in drives] == ["/"]


def test_get_available_drives_uses_mac_disks_on_mac(mac, monkeypatch):
    monkeypatch.setattr(scanner_mod, "get_mac_drives", lambda: [{"drive": "/"}])
    assert scanner_mod.get_available_drives() == [{"drive": "/"}]


def test_finder_reveal_and_trash_on_mac(mac, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main
    launched = []
    monkeypatch.setattr(main.subprocess, "Popen", lambda args: launched.append(args))
    f = tmp_path / "a.tmp"
    f.write_text("x")
    client = TestClient(main.app, base_url="http://127.0.0.1")
    assert client.post("/api/system/open-explorer", json={"path": str(f)}).status_code == 200
    assert client.post("/api/system/open-explorer", json={"path": str(tmp_path)}).status_code == 200
    assert client.post("/api/system/open-recycle-bin").status_code == 200
    assert launched == [["open", "-R", str(f)], ["open", str(tmp_path)],
                        ["open", os.path.expanduser("~/.Trash")]]
    info = client.get("/api/system/info").json()
    assert info["platform"] == "macos" and info["trash"] == "Trash" and info["file_manager"] == "Finder"


def test_mac_folder_picker_returns_the_chosen_folder(mac, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main

    class Done:
        stdout = str(tmp_path) + "/\n"
    calls = []
    monkeypatch.setattr(main.subprocess, "run", lambda args, **kw: calls.append(args) or Done())
    assert TestClient(main.app, base_url="http://127.0.0.1").post("/api/system/browse-folder").json() == {"path": str(tmp_path)}
    assert calls[0][0] == "osascript"


def test_windows_wording_is_unchanged_off_mac(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    terms = osinfo.platform_terms()
    assert terms["platform"] == "windows" and terms["trash"] == "Recycle Bin"
    assert terms["file_manager"] == "Windows Explorer" and terms["scheduling_supported"]


def test_data_folder_on_mac_is_application_support(monkeypatch, tmp_path):
    from app.engine import storage
    monkeypatch.delenv("JUNKZERO_DATA_DIR", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(storage.sys, "platform", "darwin")
    monkeypatch.setattr(storage.Path, "home", classmethod(lambda cls: tmp_path))
    assert storage.data_dir() == tmp_path / "Library" / "Application Support" / "JunkZero"


# ------------------------------------------------------------- Scheduled scans (launchd)

def test_launchd_plist_runs_weekly_and_daily():
    weekly = plistlib.loads(scheduler.launchd_plist("weekly", "09:30", "SUN", ["/x/JunkZero", "--mode", "report"]))
    assert weekly["Label"] == scheduler.LAUNCHD_LABEL
    assert weekly["ProgramArguments"] == ["/x/JunkZero", "--mode", "report"]
    assert weekly["StartCalendarInterval"] == {"Hour": 9, "Minute": 30, "Weekday": 0}
    assert plistlib.loads(scheduler.launchd_plist("weekly", "07:00", "MON", ["a"]))["StartCalendarInterval"]["Weekday"] == 1
    daily = plistlib.loads(scheduler.launchd_plist("daily", "23:05", "MON", ["a"]))
    assert daily["StartCalendarInterval"] == {"Hour": 23, "Minute": 5}


def test_mac_schedule_installs_and_removes_a_launch_agent(mac, monkeypatch, tmp_path):
    agent = tmp_path / "LaunchAgents" / "com.junkzero.scheduled-scan.plist"
    monkeypatch.setattr(scheduler, "launch_agent_path", lambda: agent)
    calls = []

    class Ok:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(scheduler.subprocess, "run", lambda args, **kw: calls.append(args) or Ok())
    schedule = scheduler.save_schedule("daily", "08:00", "MON", [str(tmp_path)], True)
    assert schedule["enabled"]
    job = plistlib.loads(agent.read_bytes())
    assert job["ProgramArguments"][0] == sys.executable
    assert ["launchctl", "load", "-w", str(agent)] in calls
    assert scheduler.scheduling_supported()

    assert not scheduler.delete_schedule()["enabled"]
    assert not agent.exists()
    assert ["launchctl", "unload", "-w", str(agent)] in calls
