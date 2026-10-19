"""Start menu / desktop / applications-menu shortcuts for portable and from-source copies."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from app.engine import osinfo, shortcuts, storage
from tests.helpers import api_client


@pytest.fixture
def windows(monkeypatch, tmp_path):
    """Windows with its Start menu and desktop in tmp_path; .lnk files are written as plain files."""
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    monkeypatch.setattr(osinfo, "is_linux", lambda: False)
    folders = {"start_menu": tmp_path / "Programs", "desktop": tmp_path / "Desktop"}
    for f in folders.values():
        f.mkdir()
    monkeypatch.setattr(shortcuts, "_known_folder", lambda place: folders[place])
    written = []

    def fake_lnk(path):
        path.write_text(shortcuts._target_key(), encoding="utf-8")
        written.append(path)
    monkeypatch.setattr(shortcuts, "_write_lnk", fake_lnk)
    return folders, written


@pytest.fixture
def linux(monkeypatch, tmp_path):
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(osinfo, "is_linux", lambda: True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(shortcuts, "LINUX_SYSTEM_ENTRIES", (tmp_path / "usr-share-applications",))
    return tmp_path / "share" / "applications" / "junkzero.desktop"


def frozen_at(monkeypatch, exe: Path):
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))


def test_nothing_is_offered_on_a_mac(monkeypatch):
    monkeypatch.setattr(osinfo, "is_windows", lambda: False)
    monkeypatch.setattr(osinfo, "is_linux", lambda: False)
    assert shortcuts.status() == {"supported": False, "places": [], "offer": False}
    with pytest.raises(shortcuts.ShortcutError):
        shortcuts.add(["desktop"])
    assert shortcuts.repair() == 0


def test_installer_copy_gets_no_offer(windows, monkeypatch, tmp_path):
    exe = tmp_path / "Programs" / "JunkZero" / "JunkZero.exe"
    frozen_at(monkeypatch, exe)
    (exe.parent / "unins000.exe").write_bytes(b"")
    assert shortcuts.status()["supported"] is False
    with pytest.raises(shortcuts.ShortcutError):
        shortcuts.add(["start_menu"])


def test_deb_copy_gets_no_offer(linux, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/junkzero/JunkZero")
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: self)
    assert shortcuts.status()["supported"] is False


def test_portable_windows_copy_is_offered_both_and_adds_them(windows, monkeypatch, tmp_path):
    folders, written = windows
    exe = tmp_path / "Downloads" / "JunkZero" / "JunkZero.exe"
    frozen_at(monkeypatch, exe)
    status = shortcuts.status()
    assert status["supported"] and status["offer"]
    assert [p["id"] for p in status["places"]] == ["start_menu", "desktop"]
    status = shortcuts.add(["start_menu", "desktop"])
    assert all(p["present"] for p in status["places"]) and not status["offer"]
    assert (folders["start_menu"] / "JunkZero.lnk").read_text() == str(exe.resolve())
    assert len(written) == 2
    # Adding again leaves them alone
    shortcuts.add(["desktop"])
    assert len(written) == 2


def test_existing_shortcut_is_never_overwritten(windows, monkeypatch, tmp_path):
    folders, written = windows
    frozen_at(monkeypatch, tmp_path / "a" / "JunkZero.exe")
    (folders["desktop"] / "JunkZero.lnk").write_text("someone else's")
    status = shortcuts.status()
    assert not status["offer"]
    shortcuts.add(["start_menu", "desktop"])
    assert (folders["desktop"] / "JunkZero.lnk").read_text() == "someone else's"
    assert [p.parent.name for p in written] == ["Programs"]
    # Not made by JunkZero, so a moved copy doesn't touch it either
    frozen_at(monkeypatch, tmp_path / "b" / "JunkZero.exe")
    (tmp_path / "a" / "JunkZero.exe").unlink()
    shortcuts.repair()
    assert (folders["desktop"] / "JunkZero.lnk").read_text() == "someone else's"


def test_dismissed_offer_stays_dismissed(windows, monkeypatch, tmp_path):
    frozen_at(monkeypatch, tmp_path / "JunkZero.exe")
    assert shortcuts.status()["offer"]
    shortcuts.dismiss_offer()
    status = shortcuts.status()
    assert status["supported"] and not status["offer"]


def test_moved_portable_folder_repoints_shortcuts_it_made(windows, monkeypatch, tmp_path):
    folders, written = windows
    old = tmp_path / "old" / "JunkZero.exe"
    frozen_at(monkeypatch, old)
    shortcuts.add(["start_menu", "desktop"])
    new = tmp_path / "new" / "JunkZero.exe"
    # While the old copy is still there, the new one leaves the shortcuts alone
    frozen_at(monkeypatch, new)
    assert shortcuts.repair() == 0
    assert (folders["desktop"] / "JunkZero.lnk").read_text() == str(old.resolve())
    # Once it's gone (moved), they follow the copy that runs
    old.unlink()
    assert shortcuts.repair() == 2
    assert (folders["desktop"] / "JunkZero.lnk").read_text() == str(new.resolve())
    assert shortcuts.repair() == 0


def test_deleted_shortcut_is_forgotten_not_remade(windows, monkeypatch, tmp_path):
    folders, written = windows
    old = tmp_path / "old" / "JunkZero.exe"
    frozen_at(monkeypatch, old)
    shortcuts.add(["start_menu", "desktop"])
    (folders["desktop"] / "JunkZero.lnk").unlink()
    old.unlink()
    frozen_at(monkeypatch, tmp_path / "new" / "JunkZero.exe")
    assert shortcuts.repair() == 1
    assert not (folders["desktop"] / "JunkZero.lnk").exists()
    assert set(storage.load_settings()["shortcuts"]["made"]) == {"start_menu"}


def test_from_source_starts_the_window_without_a_console(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(osinfo, "is_windows", lambda: True)
    python = tmp_path / "Scripts" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"")
    (python.parent / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(python))
    program, args, workdir, icon = shortcuts.launch_target()
    assert program == str(python.parent / "pythonw.exe")
    assert args == ["-m", "app.main", "--mode", "gui"]
    assert workdir == str(shortcuts.REPO_ROOT)
    assert icon.endswith("JunkZero.ico") and os.path.isfile(icon)


@pytest.mark.skipif(os.name == "nt", reason="Linux paths")
def test_linux_portable_copy_adds_a_menu_entry(linux, monkeypatch, tmp_path):
    exe = tmp_path / "My Apps" / "JunkZero" / "JunkZero"
    frozen_at(monkeypatch, exe)
    icon = tmp_path / "My Apps" / "JunkZero" / "_internal" / "packaging" / "JunkZero.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"png")
    monkeypatch.setattr(sys, "_MEIPASS", str(icon.parent.parent), raising=False)
    status = shortcuts.status()
    assert status["offer"] and [p["id"] for p in status["places"]] == ["menu"]
    shortcuts.add(["menu"])
    text = linux.read_text()
    assert f'Exec="{exe.resolve()}"\n' in text
    assert f"Icon={icon}\n" in text and "Terminal=false" in text
    assert os.access(linux, os.X_OK)
    assert not shortcuts.status()["offer"]


@pytest.mark.skipif(os.name == "nt", reason="Linux paths")
def test_linux_from_source_entry_runs_the_venv_python(linux, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", "/home/al ex/JunkZero/.venv/bin/python")
    entry = shortcuts.desktop_entry()
    assert 'Exec="/home/al ex/JunkZero/.venv/bin/python" -m app.main --mode gui\n' in entry
    assert f"Path={shortcuts.REPO_ROOT}\n" in entry
    assert "Icon=" + str(shortcuts.REPO_ROOT / "packaging" / "JunkZero.png") in entry


def test_desktop_exec_quoting():
    assert shortcuts._desktop_exec_arg("/opt/a") == "/opt/a"
    assert shortcuts._desktop_exec_arg('/a b/$x"y') == '"/a b/\\$x\\"y"'
    assert shortcuts._desktop_exec_arg("/100%/a") == '"/100%%/a"'


def test_debs_menu_entry_counts_as_present(linux, monkeypatch, tmp_path):
    frozen_at(monkeypatch, tmp_path / "portable" / "JunkZero")
    system = tmp_path / "usr-share-applications"
    system.mkdir()
    (system / "junkzero.desktop").write_text("[Desktop Entry]\n")
    status = shortcuts.status()
    assert status["places"][0]["present"] and not status["offer"]
    shortcuts.add(["menu"])
    assert not linux.exists()  # a user entry with the same name would hide the .deb's


def test_api(windows, monkeypatch, tmp_path):
    frozen_at(monkeypatch, tmp_path / "JunkZero.exe")
    client = api_client()
    assert client.get("/api/shortcuts").json()["offer"] is True
    assert client.post("/api/shortcuts", json={"places": ["nowhere"]}).status_code == 400
    body = client.post("/api/shortcuts", json={"places": ["desktop"]}).json()
    assert [p["present"] for p in body["places"]] == [False, True]
    assert client.post("/api/shortcuts/dismiss").json()["offer"] is False


@pytest.mark.skipif(os.name != "nt", reason="Real Windows shortcuts")
def test_real_windows_shortcut(monkeypatch, tmp_path):
    """The PowerShell script makes a working .lnk that starts this copy."""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    lnk = tmp_path / "JunkZero.lnk"
    shortcuts._write_lnk(lnk)
    assert lnk.exists()
    read = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:L);"
            "Write-Output $s.TargetPath; Write-Output $s.Arguments; Write-Output $s.WorkingDirectory")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", read],
                         env={**os.environ, "L": str(lnk)}, capture_output=True, text=True, timeout=60)
    target, args, workdir = out.stdout.strip().splitlines()[:3]
    program, expected_args, expected_dir, _ = shortcuts.launch_target()
    assert os.path.normcase(target) == os.path.normcase(program)
    assert args == "-m app.main --mode gui"
    assert os.path.normcase(workdir) == os.path.normcase(expected_dir)


@pytest.mark.skipif(os.name != "nt", reason="Real Windows folders")
def test_real_windows_known_folders():
    for place in ("start_menu", "desktop"):
        folder = shortcuts._known_folder(place)
        assert folder is not None and folder.is_absolute()
