"""Integration tests for the FastScanner traversal engine."""
import os
import tempfile
import pytest
from pathlib import Path
from app.config import ScanOptions
from app.engine.scanner import FastScanner


def test_fast_scanner_traversal():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Create simulated garbage files
        (base / "downloads").mkdir()
        (base / "downloads" / "installer_setup.exe").write_text("dummy exe content")
        (base / "downloads" / "game.apk").write_text("dummy apk content")
        (base / "downloads" / "unfinished.crdownload").write_text("partial download")

        (base / "workspace").mkdir()
        (base / "workspace" / "Main.class").write_text("dummy bytecode")
        (base / "workspace" / "temp.log").write_text("some logs")

        # Regular file that should not be flagged as garbage
        (base / "workspace" / "document.txt").write_text("important user document")

        options = ScanOptions(
            target_path=tmpdir,
            skip_system_dirs=True
        )

        scanner = FastScanner(options)
        items = scanner.run_scan()

        found_names = {i.name for i in items}
        assert "installer_setup.exe" in found_names
        assert "game.apk" in found_names
        assert "unfinished.crdownload" in found_names
        assert "Main.class" in found_names
        assert "temp.log" in found_names
        assert "document.txt" not in found_names

        assert scanner.stats.garbage_items_found >= 5
        assert scanner.stats.is_completed is True


def test_disabled_build_category_still_scans_contents(tmp_path):
    build = tmp_path / 'build'
    build.mkdir()
    (build / 'junk.tmp').write_text('junk')
    scanner = FastScanner(ScanOptions(target_path=str(tmp_path), include_java_builds=False))
    assert [item.name for item in scanner.run_scan()] == ['junk.tmp']


def test_repeated_scan_resets_counters(tmp_path):
    (tmp_path / 'junk.tmp').write_text('junk')
    scanner = FastScanner(ScanOptions(target_path=str(tmp_path)))
    scanner.run_scan()
    scanner.run_scan()
    assert scanner.stats.garbage_items_found == 1
    assert scanner.stats.total_files_scanned == 1
    assert scanner.stats.total_garbage_bytes == 4


def test_cancel_before_worker_starts_is_preserved(tmp_path):
    (tmp_path / 'junk.tmp').write_text('junk')
    scanner = FastScanner(ScanOptions(target_path=str(tmp_path)))
    scanner.cancel()
    assert scanner.run_scan() == []
    assert scanner.stats.is_cancelled
    assert not scanner.stats.is_completed
