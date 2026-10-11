"""Unit tests for the Garbage Item Classifier."""
import pytest
from app.config import (
    ScanOptions,
    CAT_INSTALLERS,
    CAT_JAVA_BUILDS,
    CAT_TEMP_JUNK,
    CAT_BROKEN_DOWNLOADS,
    RISK_SAFE,
    RISK_REVIEW,
    RISK_CAUTION
)
from app.engine.classifier import classify_item, is_system_protected_path


@pytest.fixture
def default_options():
    return ScanOptions(target_path="C:/Users/Test")


def test_classify_apk(default_options):
    item = classify_item(
        path="C:/Users/Test/Downloads/myapp.apk",
        name="myapp.apk",
        size_bytes=15000000,
        mtime=1600000000.0,
        is_dir=False,
        options=default_options
    )
    assert item is not None
    assert item.category == CAT_INSTALLERS
    assert item.risk_level == RISK_REVIEW
    assert "Android" in item.reason


def test_classify_java_class(default_options):
    item = classify_item(
        path="C:/Users/Test/projects/App.class",
        name="App.class",
        size_bytes=4096,
        mtime=1600000000.0,
        is_dir=False,
        options=default_options
    )
    assert item is not None
    assert item.category == CAT_JAVA_BUILDS
    assert item.risk_level == RISK_SAFE
    assert ".class" in item.reason


def test_classify_installer_exe(default_options):
    item = classify_item(
        path="C:/Users/Test/Downloads/NodeJS_v20_setup.exe",
        name="NodeJS_v20_setup.exe",
        size_bytes=35000000,
        mtime=1600000000.0,
        is_dir=False,
        options=default_options
    )
    assert item is not None
    assert item.category == CAT_INSTALLERS
    assert item.risk_level == RISK_REVIEW


def test_classify_temp_and_broken_download(default_options):
    tmp_item = classify_item(
        path="C:/Users/Test/AppData/Local/Temp/scratch.tmp",
        name="scratch.tmp",
        size_bytes=1024,
        mtime=1600000000.0,
        is_dir=False,
        options=default_options
    )
    assert tmp_item is not None
    assert tmp_item.category == CAT_TEMP_JUNK
    assert tmp_item.risk_level == RISK_SAFE

    cr_item = classify_item(
        path="C:/Users/Test/Downloads/bigfile.iso.crdownload",
        name="bigfile.iso.crdownload",
        size_bytes=500000,
        mtime=1600000000.0,
        is_dir=False,
        options=default_options
    )
    assert cr_item is not None
    assert cr_item.category == CAT_BROKEN_DOWNLOADS
    assert cr_item.risk_level == RISK_SAFE


def test_classify_os_iso_and_setup_zip(default_options, tmp_path):
    iso_item = classify_item(
        path="C:/Users/Test/Downloads/Ubuntu_24_04_LTS.iso",
        name="Ubuntu_24_04_LTS.iso",
        size_bytes=4500000000,
        mtime=1600000000.0,
        is_dir=False,
        options=default_options
    )
    assert iso_item is not None
    assert iso_item.category == CAT_INSTALLERS
    assert "ISO" in iso_item.reason

    import zipfile
    setup_zip = tmp_path / "Downloads" / "VSCode_portable_win64_setup.zip"
    setup_zip.parent.mkdir()
    with zipfile.ZipFile(setup_zip, "w") as zf:
        zf.writestr("VSCodeSetup-x64.exe", b"MZ")
    zip_item = classify_item(
        path=str(setup_zip),
        name="VSCode_portable_win64_setup.zip",
        size_bytes=120000000,
        mtime=1600000000.0,
        is_dir=False,
        options=default_options
    )
    assert zip_item is not None
    assert zip_item.category == CAT_INSTALLERS
    assert "archive" in zip_item.reason.lower()


def test_system_protection_block():
    assert is_system_protected_path("C:/Windows/System32/kernel32.dll") is True
    assert is_system_protected_path("C:/Program Files/Google/Chrome/chrome.exe") is True
    assert is_system_protected_path("D:/$Recycle.Bin/somefile") is True
    assert is_system_protected_path("C:/Users/Jagad/Downloads/setup.exe") is False


def test_system_protection_normalizes_parent_segments():
    assert is_system_protected_path('C:/Users/../Windows/System32/kernel32.dll')
    assert is_system_protected_path('C:/ProgramData/Microsoft/cache.tmp')
    assert not is_system_protected_path('C:/WindowsBackups/setup.exe')


def test_stale_large_threshold_matches_config(default_options):
    import time
    from app.config import LARGE_FILE_BYTES_THRESHOLD, CAT_STALE_LARGE
    old = time.time() - 200 * 86400
    assert classify_item('C:/Users/Test/video.dat', 'video.dat',
                         LARGE_FILE_BYTES_THRESHOLD - 1, old, False, default_options) is None
    item = classify_item('C:/Users/Test/video.dat', 'video.dat',
                         LARGE_FILE_BYTES_THRESHOLD, old, False, default_options)
    assert item.category == CAT_STALE_LARGE
