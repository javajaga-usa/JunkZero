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


def test_deb_uninstall_removes_the_linux_schedule_by_its_real_names():
    from app.engine import linux_scheduler
    postrm = (PACKAGING / "linux" / "postrm").read_text(encoding="utf-8")
    assert linux_scheduler.CRON_MARKER + "$" in postrm
    assert f"{linux_scheduler.UNIT_NAME}.service" in postrm and f"{linux_scheduler.UNIT_NAME}.timer" in postrm
    desktop = (PACKAGING / "linux" / "junkzero.desktop").read_text(encoding="utf-8")
    assert "Exec=junkzero\n" in desktop and "Icon=junkzero\n" in desktop


def test_linux_build_opens_in_the_browser_and_ships_a_deb():
    spec = (PACKAGING / "JunkZero.spec").read_text(encoding="utf-8")
    assert '["webview"] if sys.platform.startswith("linux")' in spec
    build = (PACKAGING / "linux" / "build-linux.sh").read_text(encoding="utf-8")
    assert "JunkZero-Linux-x86_64.tar.gz" in build and "_amd64.deb" in build


def test_linux_build_bundles_the_menu_entry_icon():
    spec = (PACKAGING / "JunkZero.spec").read_text(encoding="utf-8")
    assert '(os.path.join(SPECPATH, "JunkZero.png"), "packaging")' in spec


def test_launchers_pass_their_arguments_on():
    root = PACKAGING.parent
    assert (root / "run.bat").read_text(encoding="utf-8").rstrip().splitlines()[-3].endswith("--mode gui %*")
    assert (root / "run.command").read_text(encoding="utf-8").rstrip().endswith('--mode gui "$@"')
    assert (root / "run.sh").read_text(encoding="utf-8").rstrip().endswith('--mode gui "$@"')
