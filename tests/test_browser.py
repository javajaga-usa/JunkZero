"""End-to-end tests in a real browser: start JunkZero in server mode, scan a folder with junk,
delete it permanently from the page, and check the files are gone from disk. Endpoints a
newer server adds (delete progress, cancel) are answered as the shared contract says.

Needs Python Playwright and Chromium (CI installs them; skipped when they are missing).
"""
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def server(tmp_path):
    """JunkZero in --mode server; yields the tokened address it prints."""
    env = {**os.environ, "JUNKZERO_DATA_DIR": str(tmp_path / "data"), "PYTHONUNBUFFERED": "1"}
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main", "--mode", "server", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        url = None
        deadline = time.time() + 30
        while time.time() < deadline and url is None:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            match = re.search(r"Open JunkZero at (\S+)", line)
            if match:
                url = match.group(1)
        assert url, "JunkZero did not print its address"
        for _ in range(100):  # wait until the server answers
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture()
def page():
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:  # browser not installed here
            pytest.skip(f"Chromium is not available: {e}")
        try:
            yield browser.new_page()
        finally:
            browser.close()


def _junk_folder(tmp_path: Path) -> list:
    """Three junk files (old temp files, a broken download) and one file to keep, last."""
    target = tmp_path / "junk-target"
    target.mkdir()
    files = []
    for name in ("old-1.tmp", "old-2.tmp", "setup.crdownload"):
        f = target / name
        f.write_bytes(b"x" * 2048)
        old = time.time() - 90 * 86400
        os.utime(f, (old, old))
        files.append(f)
    keep = target / "notes.txt"
    keep.write_text("keep me")
    return files + [keep]


def _open(page, url) -> None:
    page.goto(url)
    page.wait_for_function("document.getElementById('appVersion').textContent.startsWith('v')")


def _scan(page, folder: Path, names) -> None:
    """Scan a folder from the page (Enter in the path box starts it) and wait for the results."""
    page.fill("#targetPathInput", str(folder))
    page.press("#targetPathInput", "Enter")
    page.wait_for_function("document.getElementById('statusText').textContent === 'Scan Finished'", timeout=30000)
    for name in names:
        assert page.locator("#fileTableBody tr", has_text=name).count() == 1, name


def _confirm_delete(page) -> None:
    page.check("#masterCheckbox")
    page.click("#btnDeleteItems")
    page.wait_for_selector("#confirmModal:not(.hidden)")
    if page.is_visible("#confirmPermanentAckInput"):
        page.check("#confirmPermanentAckInput")
    # The delete preview decides whether DELETE must be typed
    page.wait_for_function("!document.getElementById('btnExecuteDelete').disabled"
                           " || !document.getElementById('confirmTyped').classList.contains('hidden')")
    if page.is_visible("#confirmTypedInput"):
        page.fill("#confirmTypedInput", "DELETE")
    page.wait_for_function("!document.getElementById('btnExecuteDelete').disabled")
    page.click("#btnExecuteDelete")


def test_scan_and_permanent_delete_in_the_browser(server, page, tmp_path):
    *junk, keep = _junk_folder(tmp_path)
    _open(page, server)
    _scan(page, junk[0].parent, [f.name for f in junk])
    assert page.locator("#fileTableBody tr", has_text=keep.name).count() == 0

    # Sort headers are reachable from the keyboard
    page.focus("th[data-sort='name'] .sort-btn")
    page.keyboard.press("Enter")
    assert page.get_attribute("th[data-sort='name']", "aria-sort") == "ascending"

    page.click("#modePermanent")
    _confirm_delete(page)
    page.wait_for_selector("#confirmModal", state="hidden", timeout=30000)

    for f in junk:
        assert not f.exists(), f"{f.name} was not deleted"
    assert keep.exists()
    assert page.locator("#fileTableBody tr", has_text=junk[0].name).count() == 0
    # Nothing is still marked as deleting, so closing the window doesn't ask
    assert page.evaluate("window.onbeforeunload === null")


def test_delete_progress_cancel_and_error_details(server, page, tmp_path):
    """Progress, Cancel, skipped items and a sticky error toast whose details list every
    path and reason (the delete endpoints are answered here as the contract says)."""
    *junk, _keep = _junk_folder(tmp_path)
    _open(page, server)
    _scan(page, junk[0].parent, [f.name for f in junk])

    pending, cancelled = [], []
    page.route("**/api/clean", lambda route: pending.append(route))
    page.route("**/api/clean/progress",
               lambda route: route.fulfill(json={"running": True, "done": 1200, "total": 3000}))
    page.route("**/api/clean/cancel",
               lambda route: (cancelled.append(True), route.fulfill(json={"status": "cancelling"})))
    _confirm_delete(page)
    page.wait_for_function(
        "document.getElementById('deleteProgressText').textContent === 'Deleting 1,200 of 3,000'")
    assert page.evaluate("document.getElementById('deleteProgressBar').style.width") == "40%"
    assert page.evaluate("typeof window.onbeforeunload") == "function"
    # The dialog can't be closed while the delete runs
    page.keyboard.press("Escape")
    assert page.is_visible("#confirmModal")

    page.click("#btnCancelClean")
    page.wait_for_function("document.getElementById('btnCancelClean').textContent.startsWith('Stopping')")
    assert cancelled
    pending[0].fulfill(json={
        "total_requested": 3, "deleted_count": 1, "failed_count": 1, "skipped_count": 1,
        "freed_bytes": 2048, "freed_formatted": "2.00 KB", "mode": "recycle_bin", "held": {},
        "errors": [{"path": str(junk[1]), "error": "In use by another program"}],
    })
    page.wait_for_selector("#confirmModal", state="hidden")
    assert page.evaluate("window.onbeforeunload === null")

    error_toast = page.locator(".toast-error", has_text="1 item couldn't be removed")
    assert error_toast.count() == 1
    assert page.locator(".toast", has_text="Cancelled. 1 item was not deleted").count() == 1
    # Without the skipped paths the page can't know which rows are gone, so they stay
    assert page.locator("#fileTableBody tr", has_text=junk[0].name).count() == 1
    # Error toasts stay until dismissed
    page.wait_for_timeout(4000)
    assert error_toast.count() == 1
    error_toast.get_by_text("Show details").click()
    page.wait_for_selector("#errorsModal:not(.hidden)")
    assert str(junk[1]) in page.text_content("#errorsList")
    assert "In use by another program" in page.text_content("#errorsList")
    page.keyboard.press("Escape")
    page.wait_for_selector("#errorsModal", state="hidden")
    error_toast.locator(".toast-close").click()
    assert error_toast.count() == 0


def test_scan_progress_steps_and_a_dropped_stream(server, page):
    """Progress ticks show the current step; when the stream drops, the page asks
    /api/scan/status and reports the scan's error instead of "Scan stopped"."""
    status = {"is_running": True, "error": ""}
    page.route("**/api/scan/start", lambda route: route.fulfill(json={"status": "started"}))
    tick = ('data: {"type": "progress", "stats": {"files_scanned": 1234, "dirs_scanned": 56, '
            '"garbage_count": 0, "garbage_bytes": 0, "elapsed_seconds": 1.0, "phase": "empty_folders"}}\n\n')
    page.route("**/api/scan/stream*", lambda route: route.fulfill(
        status=200, headers={"Content-Type": "text/event-stream"}, body=tick))
    page.route("**/api/scan/status", lambda route: route.fulfill(json={
        "status": "running" if status["is_running"] else "finished", "is_running": status["is_running"],
        "files_scanned": 2000, "dirs_scanned": 80, "garbage_count": 0, "garbage_bytes": 0,
        "category_counts": {}, "category_bytes": {}, "elapsed_seconds": 2.0,
        "phase": "duplicates", "error": status["error"],
    }))
    _open(page, server)
    page.click("#btnStartScan")
    page.wait_for_function("document.getElementById('scanProgressText').textContent === 'Comparing duplicates...'")
    assert page.text_content("#scanMetricsFiles") == "2,000 files scanned"
    assert page.text_content("#statusText") != "Scan Finished"

    status.update(is_running=False, error="The disk was removed")
    page.wait_for_function("document.getElementById('statusText').textContent === 'Scan failed'", timeout=10000)
    assert page.locator(".toast-error", has_text="The disk was removed").count() == 1


def test_history_lists_stored_errors(server, page):
    page.route("**/api/history", lambda route: route.fulfill(json={"history": [{
        "timestamp": 1700000000, "mode": "recycle_bin", "deleted_count": 2, "failed_count": 1,
        "freed_bytes": 10, "source": "scan results", "paths": ["/x/a.tmp", "/x/b.tmp"], "restored": [],
        "errors": [{"path": "/x/c.tmp", "error": "Access is denied"}],
    }]}))
    _open(page, server)
    page.click("#btnOpenHistory")
    page.wait_for_selector(".history-errors")
    page.click(".history-errors summary")
    assert "/x/c.tmp" in page.text_content(".history-errors")
    assert "Access is denied" in page.text_content(".history-errors")


def test_dialogs_close_with_escape_and_give_focus_back(server, page):
    _open(page, server)
    page.focus("#btnOpenAbout")
    page.keyboard.press("Enter")
    page.wait_for_selector("#aboutModal:not(.hidden)")
    dialog = page.locator("#aboutModal [role='dialog']")
    assert dialog.get_attribute("aria-modal") == "true"
    assert page.text_content("#aboutModalTitle") == "About JunkZero"
    # Tab stays inside the dialog
    for _ in range(6):
        page.keyboard.press("Tab")
        assert page.evaluate("document.getElementById('aboutModal').contains(document.activeElement)")
    page.keyboard.press("Escape")
    page.wait_for_selector("#aboutModal", state="hidden")
    assert page.evaluate("document.activeElement.id") == "btnOpenAbout"
    # Toasts are announced
    assert page.get_attribute("#toastContainer", "aria-live") == "polite"
    # This server lists no admin-only Windows junk, so that panel stays hidden
    assert page.is_hidden("#cleanupHints")


def test_admin_cleanup_hints_are_listed_read_only(server, page):
    page.route("**/api/system/cleanup-hints", lambda route: route.fulfill(json={"hints": [
        {"name": "Windows Update downloads", "path": "C:\\Windows\\SoftwareDistribution\\Download",
         "size_bytes": 3 * 1024 ** 3},
    ]}))
    _open(page, server)
    page.wait_for_selector("#cleanupHints:not(.hidden)")
    text = page.text_content("#cleanupHints")
    assert "Windows Update downloads" in text and "3.00 GB" in text and "Disk Cleanup" in text
    assert page.locator("#cleanupHints button, #cleanupHints input").count() == 0
