"""User junk rules and "new since last scan"."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import CAT_CUSTOM_RULES, CAT_INSTALLERS, CAT_TEMP_JUNK, RISK_REVIEW, ScanOptions
from app.engine import changes, scheduler, storage
from app.engine.exclusions import ExclusionMatcher, rule_too_broad
from app.engine.scanner import FastScanner

client = TestClient(main.app, base_url="http://127.0.0.1")


def _scan(**kwargs):
    return FastScanner(ScanOptions(**kwargs)).run_scan()


# ------------------------------------------------------------- Junk rules

def test_matcher_reports_the_rule_as_written():
    m = ExclusionMatcher(["*.BAK2", r"C:\Scratch"])
    assert m.match(r"D:\x\notes.bak2", "notes.bak2") == "*.BAK2"
    assert m.match(r"c:\scratch\a\b.txt") == r"C:\Scratch"
    assert m.match(r"C:\Scratchpad\b.txt") is None


@pytest.mark.parametrize("rule,broad", [
    ("*", True), ("*.*", True), ("**/*", True), ("?*", True), ("C:\\", True), ("c:", True), ("/", True),
    ("*.bak2", False), ("render_*", False), (r"C:\Users\me\scratch", False), ("*.[ch]", False),
])
def test_rules_that_flag_everything_are_too_broad(rule, broad):
    assert rule_too_broad(rule) is broad


def test_junk_rules_flag_files_and_folders_for_review(tmp_path):
    (tmp_path / "notes.bak2").write_text("x" * 10)
    (tmp_path / "keep.txt").write_text("x")
    (tmp_path / "renders" / "deep").mkdir(parents=True)
    (tmp_path / "renders" / "deep" / "frame.png").write_bytes(b"x" * 100)
    items = _scan(target_path=str(tmp_path), custom_rules=["*.bak2", str(tmp_path / "renders")])
    by_name = {i.name: i for i in items}
    assert set(by_name) == {"notes.bak2", "renders"}
    folder = by_name["renders"]
    assert (folder.category, folder.is_directory, folder.size_bytes) == (CAT_CUSTOM_RULES, True, 100)
    bak = by_name["notes.bak2"]
    assert (bak.risk_level, bak.selected) == (RISK_REVIEW, False)
    assert "*.bak2" in bak.reason


def test_built_in_categories_and_exclusions_win_over_junk_rules(tmp_path):
    (tmp_path / "setup.exe").write_bytes(b"x")
    (tmp_path / "keep.bak2").write_bytes(b"x")
    items = _scan(target_path=str(tmp_path), custom_rules=["*.exe", "*.bak2"], exclusions=["keep.*"])
    assert [(i.name, i.category) for i in items] == [("setup.exe", CAT_INSTALLERS)]


def test_junk_rules_can_be_turned_off_for_a_scan(tmp_path):
    (tmp_path / "notes.bak2").write_bytes(b"x")
    assert _scan(target_path=str(tmp_path), custom_rules=["*.bak2"], include_custom_rules=False) == []


def test_junk_rules_api_round_trip_and_rejects_broad_rules():
    assert client.get("/api/custom-rules").json() == {"rules": []}
    assert client.post("/api/custom-rules/add", json={"rule": " *.bak2 "}).json() == {"rules": ["*.bak2"]}
    assert client.post("/api/custom-rules/add", json={"rule": "*.bak2"}).json() == {"rules": ["*.bak2"]}
    res = client.post("/api/custom-rules/add", json={"rule": "*.*"})
    assert res.status_code == 400 and "everything" in res.json()["detail"]
    assert client.put("/api/custom-rules", json={"rules": ["*.bak2", "*"]}).status_code == 400
    assert client.put("/api/custom-rules", json={"rules": []}).json() == {"rules": []}


def test_scan_api_and_scheduled_reports_use_junk_rules(tmp_path, monkeypatch):
    storage.set_custom_rules(["*.bak2"])
    (tmp_path / "old.bak2").write_bytes(b"x")
    report = scheduler.run_report([str(tmp_path)], include_junk_locations=False)
    assert [i["category"] for i in report["items"]] == [CAT_CUSTOM_RULES]

    seen = {}
    monkeypatch.setattr(main.FastScanner, "run_scan", lambda self: seen.update(opts=self.options) or [])
    client.post("/api/scan/start", json={"target_path": str(tmp_path), "include_custom_rules": False})
    for _ in range(50):
        if "opts" in seen:
            break
        time.sleep(0.02)
    assert (seen["opts"].custom_rules, seen["opts"].include_custom_rules) == (["*.bak2"], False)


# ------------------------------------------------------------- New since last scan

def _scan_and_compare(**kwargs):
    options = ScanOptions(**kwargs)
    scanner = FastScanner(options)
    return changes.compare_and_remember(options, scanner.run_scan())


def test_first_scan_has_nothing_to_compare_then_new_items_are_reported(tmp_path):
    (tmp_path / "a.tmp").write_text("x")
    first = _scan_and_compare(target_path=str(tmp_path))
    assert first == {"previous_scan_at": None, "new_paths": []}

    (tmp_path / "b.tmp").write_text("x")
    second = _scan_and_compare(target_path=str(tmp_path))
    assert second["previous_scan_at"] is not None
    assert second["new_paths"] == [str(tmp_path / "b.tmp")]

    # Nothing changed since: nothing is new
    assert _scan_and_compare(target_path=str(tmp_path))["new_paths"] == []


def test_turning_on_a_category_does_not_mark_its_items_new(tmp_path):
    (tmp_path / "a.tmp").write_text("x")
    (tmp_path / "setup.exe").write_text("x")
    _scan_and_compare(target_path=str(tmp_path), include_installers=False)
    result = _scan_and_compare(target_path=str(tmp_path))
    assert result["new_paths"] == []


def test_each_target_is_compared_with_its_own_previous_scan(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    (a / "x.tmp").write_text("x")
    (b / "y.tmp").write_text("x")
    _scan_and_compare(target_path=str(a))
    assert _scan_and_compare(target_path=str(b))["previous_scan_at"] is None
    assert _scan_and_compare(target_path=str(a))["new_paths"] == []


def test_scan_memory_keeps_only_recent_targets(monkeypatch):
    monkeypatch.setattr(storage, "SCAN_MEMORY_LIMIT", 2)
    for key in ("one", "two", "three"):
        storage.remember_scan(key, ["p"], [CAT_TEMP_JUNK])
    assert storage.last_scan("one") is None
    assert storage.last_scan("three")["paths"] == ["p"]


def test_truncated_scan_memory_is_not_compared(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "SCAN_MEMORY_PATHS_LIMIT", 1)
    (tmp_path / "a.tmp").write_text("x")
    (tmp_path / "b.tmp").write_text("x")
    _scan_and_compare(target_path=str(tmp_path))
    assert _scan_and_compare(target_path=str(tmp_path))["previous_scan_at"] is None


def test_completed_scan_event_carries_changes(tmp_path, monkeypatch):
    import asyncio
    workers = []

    class DeferredThread:
        def __init__(self, target, **kwargs):
            workers.append(target)

        def start(self):
            pass

    real_thread = main.threading.Thread

    async def scan_once():
        # Only the scan worker is deferred; the scanner's own thread pool needs real threads
        monkeypatch.setattr(main.threading, "Thread", DeferredThread)
        await main.api_start_scan(main.ScanRequest(target_path=str(tmp_path)))
        monkeypatch.setattr(main.threading, "Thread", real_thread)
        queue = main.event_queue
        workers.pop()()
        await asyncio.sleep(0)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        return events[-1]

    (tmp_path / "a.tmp").write_text("x")
    first = asyncio.run(scan_once())
    assert first["changes"] == {"previous_scan_at": None, "new_paths": []}
    (tmp_path / "b.tmp").write_text("x")
    assert asyncio.run(scan_once())["changes"]["new_paths"] == [str(tmp_path / "b.tmp")]
