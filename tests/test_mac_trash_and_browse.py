"""macOS: a clear fix when the Trash API is missing, and a folder picker that can't vanish."""
import os
import sys
import types

import pytest

from app import main
from app.engine import cleaner, osinfo, recycle
from tests.helpers import api_client


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: True)


def _no_foundation(monkeypatch):
    # Importing Foundation fails, as it does without pyobjc
    monkeypatch.setitem(sys.modules, "Foundation", None)


# ---------------------------------------------------------------- Trash readiness

def test_trash_is_ready_off_mac(monkeypatch):
    monkeypatch.setattr(osinfo, "is_macos", lambda: False)
    _no_foundation(monkeypatch)
    assert osinfo.mac_trash_ready() is True
    assert osinfo.setup_warning() is None


def test_mac_without_pyobjc_is_not_ready_and_says_how_to_fix(mac, monkeypatch):
    _no_foundation(monkeypatch)
    assert osinfo.mac_trash_ready() is False
    warning = osinfo.setup_warning()
    assert "pyobjc" in warning and "pip install -r requirements.txt" in warning and "run.command" in warning


def test_mac_with_pyobjc_is_ready(mac, monkeypatch):
    monkeypatch.setitem(sys.modules, "Foundation", types.ModuleType("Foundation"))
    assert osinfo.mac_trash_ready() is True
    assert osinfo.setup_warning() is None


def test_system_info_carries_the_setup_warning(mac, monkeypatch):
    _no_foundation(monkeypatch)
    info = api_client().get("/api/system/info").json()
    assert "pyobjc" in info["setup_warning"]


def _refusing_trash(monkeypatch, tmp_path):
    def refuse(path):
        raise OSError("Insufficient access privileges for operation")
    monkeypatch.setattr(cleaner.send2trash, "send2trash", refuse)
    monkeypatch.setattr(recycle, "recycle_bin_takes", lambda path, size: True)
    target = tmp_path / "old.tmp"
    target.write_text("junk")
    return target


def test_refused_trash_move_explains_the_missing_package(mac, monkeypatch, tmp_path):
    _no_foundation(monkeypatch)
    target = _refusing_trash(monkeypatch, tmp_path)
    result = cleaner.delete_items([{"path": str(target), "size_bytes": 4}])
    assert result.deleted_count == 0
    error = result.errors[0]["error"]
    assert "Insufficient access privileges" in error
    assert "pyobjc" in error and "pip install -r requirements.txt" in error
    assert target.exists()


def test_other_trash_errors_are_left_as_they_are(mac, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "Foundation", types.ModuleType("Foundation"))
    monkeypatch.setattr(cleaner.send2trash, "send2trash", lambda p: (_ for _ in ()).throw(OSError("The volume is full")))
    monkeypatch.setattr(recycle, "recycle_bin_takes", lambda path, size: True)
    target = tmp_path / "old.tmp"
    target.write_text("junk")
    error = cleaner.delete_items([{"path": str(target), "size_bytes": 4}]).errors[0]
    assert error["error"] == "The volume is full" and "kind" not in error


def test_a_permission_refusal_with_pyobjc_installed_is_explained(mac, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "Foundation", types.ModuleType("Foundation"))
    target = _refusing_trash(monkeypatch, tmp_path)
    error = cleaner.delete_items([{"path": str(target), "size_bytes": 4}]).errors[0]
    assert error["kind"] and error["error"].startswith("Can't delete:")


def test_requirements_add_pyobjc_on_mac_only():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "requirements.txt"), encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    assert any(l.startswith("pyobjc-framework-Cocoa") and 'sys_platform == "darwin"' in l for l in lines)


# ---------------------------------------------------------------- Folder picker

class _Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def _picker(monkeypatch, result):
    calls = []
    monkeypatch.setattr(main.subprocess, "run", lambda args, **kw: calls.append(args) or result)
    monkeypatch.setattr(main, "gui_window", None)
    return calls


def test_picker_comes_to_the_front_and_starts_at_the_current_folder(mac, monkeypatch, tmp_path):
    chosen = tmp_path / "Projects"
    chosen.mkdir()
    calls = _picker(monkeypatch, _Result(stdout=str(chosen) + "/\n"))
    res = api_client().post("/api/system/browse-folder", json={"start": str(tmp_path)})
    assert res.json() == {"path": str(chosen)}
    command = calls[0]
    assert command[0] == "osascript"
    script = [command[i + 1] for i, part in enumerate(command) if part == "-e"]
    assert "activate" in script
    # The start folder is an argument, never pasted into the script
    assert command[-1] == str(tmp_path)
    assert not any(str(tmp_path) in line for line in script)


def test_picker_ignores_a_start_folder_that_does_not_exist(mac, monkeypatch, tmp_path):
    calls = _picker(monkeypatch, _Result(stdout=str(tmp_path)))
    api_client().post("/api/system/browse-folder", json={"start": str(tmp_path / "gone")})
    assert calls[0][-1] == "end run"


def test_cancelling_the_picker_is_not_an_error(mac, monkeypatch):
    _picker(monkeypatch, _Result(stderr="execution error: User canceled. (-128)", returncode=1))
    assert api_client().post("/api/system/browse-folder").json() == {"path": ""}


def test_a_picker_that_cannot_open_says_so(mac, monkeypatch):
    _picker(monkeypatch, _Result(stderr="execution error: Not authorized (-1743)", returncode=1))
    data = api_client().post("/api/system/browse-folder").json()
    assert data["path"] == ""
    assert "-1743" in data["error"] and "Type the folder path" in data["error"]


def test_the_app_window_picker_is_used_when_there_is_a_window(mac, monkeypatch, tmp_path):
    asked = {}

    class Window:
        def create_file_dialog(self, dialog, directory=""):
            asked.update(dialog=dialog, directory=directory)
            return (str(tmp_path),)

    fake_webview = types.SimpleNamespace(FileDialog=types.SimpleNamespace(FOLDER="folder"))
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(main, "gui_window", Window())
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: pytest.fail("osascript should not run"))
    data = api_client().post("/api/system/browse-folder", json={"start": str(tmp_path)}).json()
    assert data == {"path": str(tmp_path)}
    assert asked == {"dialog": "folder", "directory": str(tmp_path)}


def test_window_picker_cancel_returns_no_path(mac, monkeypatch):
    class Window:
        def create_file_dialog(self, dialog, directory=""):
            return None

    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(FOLDER_DIALOG=20))
    monkeypatch.setattr(main, "gui_window", Window())
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: pytest.fail("osascript should not run"))
    assert api_client().post("/api/system/browse-folder").json() == {"path": ""}


def test_window_picker_failure_falls_back_to_the_finder_picker(mac, monkeypatch, tmp_path):
    class Window:
        def create_file_dialog(self, dialog, directory=""):
            raise RuntimeError("no dialog")

    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(FOLDER_DIALOG=20))
    calls = []
    monkeypatch.setattr(main.subprocess, "run", lambda args, **kw: calls.append(args) or _Result(stdout=str(tmp_path)))
    monkeypatch.setattr(main, "gui_window", Window())
    assert api_client().post("/api/system/browse-folder").json() == {"path": str(tmp_path)}
    assert calls and calls[0][0] == "osascript"


def test_picker_retries_without_the_start_folder_when_macos_refuses_it(mac, monkeypatch, tmp_path):
    results = [_Result(stderr="execution error: Can't make file (-1700)", returncode=1), _Result(stdout=str(tmp_path))]
    calls = []
    monkeypatch.setattr(main.subprocess, "run", lambda args, **kw: calls.append(args) or results.pop(0))
    monkeypatch.setattr(main, "gui_window", None)
    data = api_client().post("/api/system/browse-folder", json={"start": str(tmp_path)}).json()
    assert data == {"path": str(tmp_path)}
    assert calls[0][-1] == str(tmp_path) and calls[1][-1] == "end run"


def test_cancel_on_the_first_try_does_not_open_the_picker_again(mac, monkeypatch, tmp_path):
    calls = _picker(monkeypatch, _Result(stderr="User canceled. (-128)", returncode=1))
    assert api_client().post("/api/system/browse-folder", json={"start": str(tmp_path)}).json() == {"path": ""}
    assert len(calls) == 1
