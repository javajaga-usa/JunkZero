"""The installer, spec and icons stay in step with the app."""
import re
from pathlib import Path

from app.engine import scheduler

PACKAGING = Path(__file__).resolve().parent.parent / "packaging"


def test_uninstaller_removes_the_scheduled_scan_by_its_real_name():
    iss = (PACKAGING / "JunkZero.iss").read_text(encoding="utf-8")
    name = re.search(r'#define TaskName "([^"]+)"', iss).group(1)
    assert name == scheduler.TASK_NAME
    assert re.search(r'^\[UninstallRun\]\s*\n(;.*\n)*Filename: "\{sys\}\\schtasks.exe"; Parameters: "/Delete /F /TN ""\{#TaskName\}"""',
                     iss, re.M)


def test_windows_build_is_a_folder_with_icon_and_version_details():
    spec = (PACKAGING / "JunkZero.spec").read_text(encoding="utf-8")
    assert 'icon=os.path.join(SPECPATH, "JunkZero.ico")' in spec
    assert "version=version_info" in spec
    assert "exclude_binaries=True" in spec.split("else:", 1)[1]
    assert "COLLECT(" in spec.split("else:", 1)[1]


def test_icons_are_committed():
    for name in ("JunkZero.ico", "JunkZero.icns", "JunkZero.png"):
        assert (PACKAGING / name).stat().st_size > 1000
