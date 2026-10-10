"""Archives count as installers only when what is inside is clearly a software package."""
import io
import os
import tarfile
import zipfile

import pytest

from app.config import CAT_INSTALLERS, RISK_REVIEW, ScanOptions
from app.engine import smart
from app.engine.ai_advisor import analyze_item
from app.engine.archives import inspect_archive, judge_listing
from app.engine.classifier import classify_item


def _zip(path, names, encrypt=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"data")
    if encrypt:
        # zipfile cannot write encrypted archives; set the "encrypted" flag on every entry
        data = bytearray(path.read_bytes())
        for sig in (b"PK\x03\x04", b"PK\x01\x02"):
            start = 0
            while (i := data.find(sig, start)) != -1:
                flag_at = i + (6 if sig == b"PK\x03\x04" else 8)
                data[flag_at] |= 0x1
                start = i + 4
        path.write_bytes(bytes(data))
    return path


def _classify(path):
    return classify_item(str(path), path.name, os.path.getsize(path), 1_600_000_000.0, False,
                         ScanOptions(target_path=str(path.parent)))


# ------------------------------------------------------------- Listing rules

@pytest.mark.parametrize("names, found", [
    (["setup.exe"], "setup.exe"),
    (["Tool/Tool.msi", "Tool/readme.txt", "Tool/LICENSE"], "Tool.msi"),
    (["drivers/install.cmd", "drivers/nvlddmkm.sys", "drivers/nv.inf", "drivers/nv.cat"], "install.cmd"),
    (["Notepad++/notepad++.exe", "Notepad++/SciLexer.dll", "Notepad++/langs.xml"], "notepad++.exe"),
    (["Rectangle.app/Contents/MacOS/Rectangle", "Rectangle.app/Contents/Info.plist",
      "Rectangle.app/Contents/Resources/AppIcon.icns", "Rectangle.app/Contents/Resources/x.png"], "Rectangle.app"),
    (["Firefox 120.dmg"], "Firefox 120.dmg"),
    (["app-release.apk", "__MACOSX/._app-release.apk", ".DS_Store"], "app-release.apk"),
])
def test_setup_listings_are_recognized(names, found):
    verdict = judge_listing(names)
    assert verdict.is_setup and verdict.clean
    assert found in verdict.summary


@pytest.mark.parametrize("names", [
    ["Holiday/IMG_0001.jpg", "Holiday/IMG_0002.HEIC"],
    ["setup.exe", "Tax return 2024.pdf"],
    ["installer.msi", "family.jpg"],
    ["setup.exe", "notes.txt"],
    ["setup.exe", "budget.xlsx"],
    ["thesis.docx", "slides.pptx"],
    ["project/main.py", "project/util.py"],
    ["screenshots/a.png", "screenshots/b.png", "screenshots/c.png", "setup.exe"],
    ["setup.exe", "backup.zip", "more.7z", "photos.rar"],
    [],
])
def test_personal_or_unclear_listings_are_not_setup(names):
    assert not judge_listing(names).is_setup


def test_personal_files_are_named_in_summary():
    verdict = judge_listing(["setup.exe", "Passport scan.jpg"])
    assert verdict.personal_files == ["Passport scan.jpg"]
    assert "Passport scan.jpg" in verdict.summary


def test_a_few_unrecognized_files_make_it_less_certain():
    verdict = judge_listing(["setup.exe", "data1.xyz", "data2.xyz"] + [f"lib{i}.dll" for i in range(30)])
    assert verdict.is_setup and not verdict.clean
    assert "2 unrecognized files" in verdict.summary


# ------------------------------------------------------------- Reading archives

def test_unreadable_and_protected_archives_are_never_setup(tmp_path):
    broken = tmp_path / "setup.zip"
    broken.write_bytes(b"not a zip at all")
    protected = _zip(tmp_path / "installer.zip", ["setup.exe"], encrypt=True)
    rar = tmp_path / "setup.rar"
    rar.write_bytes(b"Rar!\x1a\x07\x00")
    for path in (broken, protected, rar):
        verdict = inspect_archive(str(path))
        assert not verdict.is_setup and not verdict.readable
    assert inspect_archive(str(protected)).summary == "password-protected"


def test_tar_archives_are_listed(tmp_path):
    path = tmp_path / "tool-linux-x64.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        for name in ("tool/install.sh", "tool/libtool.so"):
            info = tarfile.TarInfo(name)
            info.size = 4
            tf.addfile(info, io.BytesIO(b"data"))
    assert inspect_archive(str(path), path.stat().st_size).is_setup


def test_single_compressed_files_go_by_inner_name(tmp_path):
    image = tmp_path / "ubuntu-24.04-arm64.img.xz"
    image.write_bytes(b"x")
    notes = tmp_path / "notes.txt.gz"
    notes.write_bytes(b"x")
    assert inspect_archive(str(image)).is_setup
    assert not inspect_archive(str(notes)).is_setup


# ------------------------------------------------------------- Scan results

def test_setup_named_zip_full_of_photos_is_not_flagged(tmp_path):
    path = _zip(tmp_path / "Downloads" / "setup_backup_win64.zip", ["DCIM/IMG_0001.jpg", "DCIM/IMG_0002.jpg"])
    assert _classify(path) is None


def test_plain_named_zip_with_installer_inside_is_flagged(tmp_path):
    path = _zip(tmp_path / "Documents" / "stuff.zip", ["GoogleChromeStandaloneEnterprise64.msi"])
    item = _classify(path)
    assert item.category == CAT_INSTALLERS
    assert item.risk_level == RISK_REVIEW and not item.selected
    assert "GoogleChromeStandaloneEnterprise64.msi" in item.reason
    assert item.archive_clean


def test_smart_score_explains_what_was_inside(tmp_path):
    clean = _classify(_zip(tmp_path / "Downloads" / "a.zip", ["setup.exe", "readme.txt"]))
    mixed = _classify(_zip(tmp_path / "Downloads" / "b.zip",
                           ["setup.exe", "x.xyz", "y.xyz"] + [f"lib{i}.dll" for i in range(30)]))
    clean_score, clean_reasons = smart.SmartScorer().score(clean)
    mixed_score, mixed_reasons = smart.SmartScorer().score(mixed)
    assert "+15: looked inside: setup.exe inside, nothing personal" in clean_reasons
    assert any(r.startswith("+5: looked inside: setup.exe inside, plus 2") for r in mixed_reasons)
    assert clean_score > mixed_score


def test_file_inspector_reports_archive_contents(tmp_path):
    setup = analyze_item(str(_zip(tmp_path / "a.zip", ["setup.exe"])))
    personal = analyze_item(str(_zip(tmp_path / "b.zip", ["setup.exe", "Will.docx"])))
    assert setup.detected_type == "Setup / Software Archive" and "setup.exe" in setup.explanation
    assert personal.safety_verdict == "Review Carefully" and "Will.docx" in personal.explanation
