"""Only setup programs count as installers; portable apps, tools, games and uninstallers do not."""
import os

import pytest

from app.config import CAT_INSTALLERS, RISK_REVIEW, ScanOptions
from app.engine import smart
from app.engine.ai_advisor import analyze_item
from app.engine.classifier import classify_item
from app.engine.installers import inside_installed_program, inspect_exe
from exe_samples import make_exe, version_info


def _classify(path):
    return classify_item(str(path), path.name, os.path.getsize(path), 1_600_000_000.0, False,
                         ScanOptions(target_path=str(path.parent)))


# ------------------------------------------------------------- Fingerprints

@pytest.mark.parametrize("kwargs, evidence", [
    ({"resources": b'<assemblyIdentity name="JR.Inno.Setup" />'}, "built with Inno Setup"),
    ({"overlay": b"\xef\xbe\xad\xdeNullsoftInst" + b"\0" * 64}, "built with NSIS (Nullsoft)"),
    ({"resources": version_info(CompanyName="Flexera", Comments="InstallShield")}, "built with InstallShield"),
    ({"sections": (".text", ".wixburn")}, "WiX installer bundle"),
    ({"resources": version_info(FileDescription="Google Chrome Installer")}, 'describes itself as "Google Chrome Installer"'),
    ({"resources": version_info(FileDescription="Microsoft Visual C++ 2015-2022 Redistributable (x64)")},
     "Redistributable"),
])
def test_installer_fingerprints_are_found(tmp_path, kwargs, evidence):
    verdict = inspect_exe(str(make_exe(tmp_path / "app.exe", **kwargs)))
    assert verdict.is_installer
    assert evidence in verdict.evidence


@pytest.mark.parametrize("kwargs", [
    {},
    {"resources": version_info(FileDescription="Notepad++ : a free (GNU) source code editor")},
    {"resources": version_info(FileDescription="Minecraft Launcher", ProductName="Minecraft")},
    {"resources": version_info(FileDescription="Winamp", CompanyName="Nullsoft, Inc.")},
])
def test_programs_tools_and_games_are_not_installers(tmp_path, kwargs):
    assert not inspect_exe(str(make_exe(tmp_path / "tool.exe", **kwargs))).is_installer


def test_uninstallers_are_recognized(tmp_path):
    path = make_exe(tmp_path / "helper.exe", resources=b"JR.Inno.Setup" + version_info(FileDescription="Setup/Uninstall"))
    verdict = inspect_exe(str(path))
    assert verdict.is_uninstaller and not verdict.is_installer


def test_non_programs_and_missing_files_are_not_installers(tmp_path):
    fake = tmp_path / "setup.exe"
    fake.write_bytes(b"Inno Setup but not a program")
    assert not inspect_exe(str(fake)).is_installer
    assert not inspect_exe(str(tmp_path / "missing.exe")).is_installer


# ------------------------------------------------------------- Scan results

def test_setup_program_is_listed_with_its_evidence(tmp_path):
    item = _classify(make_exe(tmp_path / "Downloads" / "VSCodeUserSetup-x64.exe", resources=b"JR.Inno.Setup"))
    assert item.category == CAT_INSTALLERS and item.risk_level == RISK_REVIEW and not item.selected
    assert item.reason == "Setup program: built with Inno Setup"
    score, reasons = smart.SmartScorer().score(item)
    assert "+15: setup program: built with Inno Setup" in reasons


@pytest.mark.parametrize("name", ["putty.exe", "game.exe", "tool-portable.exe", "update.exe"])
def test_programs_in_downloads_are_not_listed(tmp_path, name):
    assert _classify(make_exe(tmp_path / "Downloads" / name)) is None


def test_programs_elsewhere_are_not_listed(tmp_path):
    assert _classify(make_exe(tmp_path / "Documents" / "MyTool.exe")) is None


def test_setup_named_without_fingerprint_is_a_weak_review(tmp_path):
    item = _classify(make_exe(tmp_path / "Downloads" / "driver_setup.exe"))
    assert item.category == CAT_INSTALLERS and item.installer_weak
    assert "no installer fingerprint" in item.reason
    _, reasons = smart.SmartScorer().score(item)
    assert "-10: only its name says setup; no installer fingerprint inside" in reasons
    assert _classify(make_exe(tmp_path / "Documents" / "setup.exe")) is None


def test_uninstallers_and_program_folders_are_never_listed(tmp_path):
    unins = make_exe(tmp_path / "Downloads" / "unins000.exe", resources=b"JR.Inno.Setup")
    assert _classify(unins) is None
    app_dir = tmp_path / "Downloads" / "MyApp"
    setup = make_exe(app_dir / "setup.exe", resources=b"JR.Inno.Setup")
    (app_dir / "helper.dll").write_bytes(b"x")
    assert _classify(setup) is None


@pytest.mark.parametrize("path, inside", [
    (r"C:\Users\me\AppData\Local\Discord\Update.exe", True),
    (r"C:\Program Files\App\setup.exe", True),
    (r"C:\ProgramData\Package Cache\{id}\vc_redist.x64.exe", True),
    (r"C:\Users\me\AppData\Local\Temp\chrome_installer.exe", False),
])
def test_installed_program_locations(path, inside):
    assert inside_installed_program(path) is inside


def test_file_inspector_explains_exe(tmp_path):
    setup = analyze_item(str(make_exe(tmp_path / "a.exe", resources=b"JR.Inno.Setup")))
    tool = analyze_item(str(make_exe(tmp_path / "b.exe")))
    unins = analyze_item(str(make_exe(tmp_path / "unins000.exe")))
    assert setup.detected_type == "Setup Program" and "Inno Setup" in setup.explanation
    assert tool.detected_type == "Program (not an installer)"
    assert unins.safety_verdict == "Keep File"
