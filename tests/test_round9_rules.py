"""Round 9 detection and scan engine fixes (findings 1, 3-7, 12, 13, 16 and the scanner side of 8/9/14)."""
import io
import os
import shutil
import tarfile
import tempfile
import threading
import time

import pytest

from app.config import (
    CAT_DUPLICATES,
    CAT_EMPTY_FOLDERS,
    CAT_FILE_TYPES,
    CAT_INSTALLERS,
    CAT_JAVA_BUILDS,
    CAT_LEFTOVERS,
    CAT_STALE_LARGE,
    CAT_TEMP_JUNK,
    LARGE_FILE_BYTES_THRESHOLD,
    RISK_REVIEW,
    RISK_SAFE,
    ScanOptions,
)
from app.engine import archives, duplicates, scanner as scanner_mod
from app.engine.classifier import build_item, classify_item, is_system_protected_path
from app.engine.duplicates import FileCandidate, find_duplicate_groups
from app.engine.leftovers import InstalledPrograms, find_leftovers, holds_personal_files
from app.engine.locations import system_cleanup_hints, windows_junk_locations
from app.engine.safeguards import Safeguards
from app.engine.scanner import FastScanner
from app.engine.smart import SCORE_DELETE, SmartScorer

OLD = time.time() - 3 * 365 * 86400


def _write(path, data=b"x", old=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    if old:
        os.utime(path, (OLD, OLD))
    return path


def _scan(target, **kw):
    kw.setdefault("leftovers", [])
    kw.setdefault("cloud_folders", [])
    s = FastScanner(ScanOptions(target_path=str(target), **kw))
    items = s.run_scan()
    return s, {os.path.relpath(i.path, target).replace(os.sep, "/") if str(target) else i.path: i for i in items}


@pytest.fixture
def home():
    """A folder outside any temp folder (pytest's tmp_path sits in /tmp, which counts as one)."""
    from pathlib import Path
    base = Path(tempfile.mkdtemp(prefix="round9-", dir=os.path.dirname(os.path.abspath(__file__))))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _classify(path, size=10, mtime=OLD, is_dir=False, **kw):
    path = str(path)
    return classify_item(path, os.path.basename(path), size, mtime, is_dir, ScanOptions(target_path=".", **kw))


# ------------------------------------------------------------- 1. LevelDB and app-data folders

def test_leveldb_and_app_data_logs_are_never_listed(home):
    local = home / "AppData" / "Local"
    ext = local / "Google" / "Chrome" / "User Data" / "Default" / "Local Extension Settings" / "nkbihfbeog"
    _write(ext / "000003.log")
    _write(local / "Code" / "IndexedDB" / "https_x.indexeddb.leveldb" / "000005.log")
    store = local / "Postman" / "store"
    for name in ("CURRENT", "MANIFEST-000001", "000007.log", "LOCK"):
        _write(store / name)
    _write(local / "SomeApp" / "debug.log")             # app-data folder alone is not a temp folder
    _write(home / ".config" / "tool" / "run.log")   # nor is a dot-folder
    _write(local / "Temp" / "setup.log")                # a real temp folder still is
    _write(local / "SomeApp" / "logs" / "old.log")      # and so is a log folder
    _, items = _scan(home, include_empty_folders=False)
    assert set(items) == {"AppData/Local/Temp/setup.log", "AppData/Local/SomeApp/logs/old.log"}


def test_classifier_alone_also_skips_leveldb(tmp_path):
    store = tmp_path / "cache" / "db"
    for name in ("CURRENT", "MANIFEST-000002", "000009.log"):
        _write(store / name)
    assert _classify(store / "000009.log") is None
    assert _classify("C:/Users/a/AppData/Roaming/MetaMask/Local Storage/leveldb/000003.log") is None
    assert _classify("C:/Users/a/AppData/Local/Temp/x.log").risk_level == RISK_SAFE


# ------------------------------------------------------------- 3. Empty folders never "Delete"

def test_empty_folders_are_never_marked_delete():
    item = build_item("/home/u/Photos/2025 trip", "2025 trip", CAT_EMPTY_FOLDERS, 0, OLD, RISK_SAFE, "Empty", is_dir=True)
    SmartScorer({"deleted": {f"{CAT_EMPTY_FOLDERS}|folder:2025 trip": 10}}).apply(item)
    assert item.score < SCORE_DELETE and item.recommendation != "Delete" and not item.selected


# ------------------------------------------------------------- 4. Wallets and app databases in leftovers

@pytest.mark.parametrize("layout", [
    {"exodus.wallet/seed.seco": b"x"},
    {"keystore/UTC--2020": b"x"},
    {"keys/id.ppk": b"x"},
    {"cert.pfx": b"x"},
    {"db/CURRENT": b"x", "db/MANIFEST-000001": b"x"},
    {"data/app.sqlite": b"x"},
    {"store.db": b"x"},
])
def test_leftovers_with_wallets_keys_or_databases_hold_personal_files(tmp_path, layout):
    for rel, data in layout.items():
        _write(tmp_path / "Old" / rel, data)
    assert holds_personal_files(str(tmp_path / "Old"))


def test_wallet_apps_are_never_offered_as_leftovers(tmp_path):
    for name in ("Exodus", "atomic", "Electrum", "Ledger Live", "OldTool"):
        _write(tmp_path / name / "settings.json")
        os.utime(tmp_path / name, (OLD, OLD))
    installed = InstalledPrograms(["Some Editor", "Other Program", "Third App"])
    found = find_leftovers([str(tmp_path)], installed, now=time.time())
    assert [l.name for l in found] == ["OldTool"]


# ------------------------------------------------------------- 5. .idb / .tlog and Debug/Release folders

def test_idb_and_tlog_are_safe_only_in_project_build_folders(tmp_path):
    proj = tmp_path / "proj"
    _write(proj / "App.sln", "")
    assert _classify(proj / "x64" / "Debug" / "App.tlog" / "CL.read.1.tlog").risk_level == RISK_SAFE
    assert _classify(proj / "x64" / "Release" / "vc.idb").risk_level == RISK_SAFE
    assert _classify(tmp_path / "re" / "sample.idb").risk_level == RISK_REVIEW
    assert _classify(tmp_path / "es" / "translog-1.tlog").risk_level == RISK_REVIEW
    # Models/Release/character.obj with no project around it
    assert _classify(tmp_path / "Models" / "Release" / "character.obj").risk_level == RISK_REVIEW


# ------------------------------------------------------------- 6. Name-only detections

def test_iso_needs_its_signature(tmp_path):
    fake = _write(tmp_path / "Downloads" / "weights.iso", b"\0" * 40000)
    iso = _write(tmp_path / "Downloads" / "ubuntu.iso", b"\0" * 0x8001 + b"CD001" + b"\0" * 100)
    udf = _write(tmp_path / "Downloads" / "movie.iso", b"\0" * (0x8001 + 2048) + b"NSR02")
    assert _classify(fake) is None
    assert _classify(iso).category == CAT_INSTALLERS
    assert _classify(udf).category == CAT_INSTALLERS


def test_bin_img_mdf_need_a_cue_sheet_next_to_them(tmp_path):
    games = tmp_path / "Games"
    assert _classify(_write(games / "model.bin")) is None
    assert _classify(_write(games / "sdcard.img")) is None
    _write(games / "disc.cue")
    assert _classify(_write(games / "disc.bin")).category == CAT_INSTALLERS
    assert _classify(games / "disc.cue") is None  # The sheet alone is not listed
    _write(games / "old.mds")
    assert _classify(_write(games / "old.mdf")).category == CAT_INSTALLERS
    sdk = tmp_path / "Android" / "Sdk" / "system-images"
    _write(sdk / "system.cue")
    assert _classify(_write(sdk / "system.bin")) is None
    appdata = tmp_path / "AppData" / "Local" / "emu"
    _write(appdata / "rom.cue")
    assert _classify(_write(appdata / "rom.bin")) is None


def test_loose_jars_are_listed_only_in_download_temp_or_target(home):
    assert _classify(home / "Minecraft" / "mods" / "sodium.jar") is None
    assert _classify(home / ".m2" / "repository" / "lib.jar") is None
    assert _classify(home / "Downloads" / "tool.jar").risk_level == RISK_REVIEW


def test_build_folders_need_a_project_file_next_to_them(tmp_path):
    home = tmp_path
    _write(home / "Videos" / "out" / "wedding.mp4")
    _write(home / ".venv" / "bin" / "python")
    _write(home / ".gradle" / "gradle.properties")
    _write(home / "web" / "package.json", "{}")
    _write(home / "web" / "build" / "main.js")
    _write(home / "rust" / "Cargo.toml", "")
    _write(home / "rust" / "target" / "debug" / "app")
    _, items = _scan(home, include_empty_folders=False)
    builds = {k for k, i in items.items() if i.category == CAT_JAVA_BUILDS and i.is_directory}
    assert builds == {"web/build", "rust/target"}
    assert not any(k.startswith(("Videos", ".venv", ".gradle")) for k in items)


def test_package_cache_is_never_scanned_even_by_file_type_search(home):
    _write(home / "ProgramData" / "Package Cache" / "{guid}" / "vc_redist.msi")
    _write(home / "Package Cache" / "x" / "setup.log")
    store = home / "AppData" / "Roaming" / "Slack" / "Local Storage" / "leveldb"
    _write(store / "000003.log")
    _write(home / "Documents" / "notes.log")
    _write(home / "Downloads" / "tool.msi")
    _, items = _scan(home, include_file_types=True, file_types=["log", "msi"], include_empty_folders=False)
    assert set(items) == {"Documents/notes.log", "Downloads/tool.msi"}
    assert items["Documents/notes.log"].category == CAT_FILE_TYPES


def test_duplicates_skip_games_venvs_models_and_installed_programs(home):
    data = os.urandom(1024 * 1024 + 10)
    for rel in ("steamapps/common/Game/a.pak", "steamapps/common/Game2/a.pak",
                ".ollama/models/blob1", "Backup/.ollama/models/blob1",
                ".venv/lib/x.so", "venv2/.venv/lib/x.so",
                "AppData/Local/App1/data.bin", "AppData/Local/App2/data.bin"):
        _write(home / rel, data)
    _write(home / "Documents" / "photo.raw", data[::-1])
    _write(home / "Downloads" / "photo.raw", data[::-1])
    _, items = _scan(home, include_duplicates=True, include_empty_folders=False)
    dups = {k for k, i in items.items() if i.category == CAT_DUPLICATES}
    assert dups == {"Downloads/photo.raw"}


@pytest.mark.parametrize("rel", [".ollama/models/blobs/sha256-1", "lm-studio/models/m.gguf",
                                 ".cache/huggingface/hub/model.safetensors", "steamapps/common/G/data.big",
                                 "AppData/Local/Programs/App/resources.bin", "proj/.venv/lib/torch.so"])
def test_stale_large_skips_program_and_model_folders(tmp_path, rel):
    assert _classify(tmp_path / rel, size=LARGE_FILE_BYTES_THRESHOLD * 2) is None


def test_stale_large_still_lists_forgotten_files(tmp_path):
    item = _classify(tmp_path / "Videos" / "raw-footage.mxf", size=LARGE_FILE_BYTES_THRESHOLD * 2)
    assert item.category == CAT_STALE_LARGE


# ------------------------------------------------------------- 7. Created in the last 24 hours

def test_file_just_unpacked_with_an_old_date_is_not_preticked(tmp_path):
    temp = tmp_path / "Temp"
    f = _write(temp / "report.tmp")  # Modified date years ago, but created just now
    s, items = _scan(tmp_path, include_empty_folders=False)
    item = items["Temp/report.tmp"]
    assert item.risk_level == RISK_REVIEW and not item.selected
    assert "24 hours" in item.score_reasons[-1]


def test_safeguards_use_the_later_of_modified_and_created():
    item = build_item("/t/Temp/a.tmp", "a.tmp", CAT_TEMP_JUNK, 1, OLD, RISK_SAFE, "r")
    item.changed_timestamp = time.time() - 60
    Safeguards([]).apply(SmartScorer().apply(item))
    assert item.selected is False and item.risk_level == RISK_REVIEW


def test_location_item_uses_created_time(tmp_path):
    from app.engine.locations import JunkLocation
    temp = tmp_path / "Temp"
    _write(temp / "a.dat")
    s = FastScanner(ScanOptions(target_path="", junk_locations=[JunkLocation("t", "Temp", str(temp))],
                                scan_junk_locations=True, leftovers=[], cloud_folders=[]))
    (item,) = s.run_scan()
    assert item.risk_level == RISK_REVIEW and "24 hours" in item.reason


# ------------------------------------------------------------- 12. Duplicate hashing

def _cand(path):
    return FileCandidate(str(path), path.name, path.stat().st_size, path.stat().st_mtime)


def test_duplicates_compare_head_tail_and_middle(tmp_path):
    size = 300 * 1024
    base = bytearray(os.urandom(size))
    same = bytes(base)
    tail = bytearray(base); tail[-1] ^= 0xFF
    middle = bytearray(base); middle[size // 2] ^= 0xFF
    paths = {n: _write(tmp_path / n, d) for n, d in
             (("a", same), ("b", same), ("tail", bytes(tail)), ("middle", bytes(middle)))}
    groups = find_duplicate_groups([_cand(p) for p in paths.values()])
    assert [sorted(c.name for c in g) for g in groups] == [["a", "b"]]


def test_tail_sample_avoids_full_hash(tmp_path, monkeypatch):
    size = 3 * 1024 * 1024
    head = os.urandom(64 * 1024)
    a = _write(tmp_path / "a", head + os.urandom(size - len(head)))
    b = _write(tmp_path / "b", head + os.urandom(size - len(head)))
    calls = []
    real = duplicates._hash
    monkeypatch.setattr(duplicates, "_hash", lambda p, limit, stop, offset=0: calls.append(limit) or real(p, limit, stop, offset))
    assert find_duplicate_groups([_cand(a), _cand(b)]) == []
    assert None not in calls  # Never read in full: the last 64 KB already differ


def test_duplicate_groups_are_hashed_in_parallel(tmp_path, monkeypatch):
    active, peak, lock = [0], [0], threading.Lock()
    real = duplicates._same_size_groups

    def slow(size, files, stop):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.1)
        try:
            return real(size, files, stop)
        finally:
            with lock:
                active[0] -= 1

    monkeypatch.setattr(duplicates, "_same_size_groups", slow)
    cands = []
    for i in range(8):
        data = os.urandom(1000 + i)
        cands += [_cand(_write(tmp_path / f"{i}a", data)), _cand(_write(tmp_path / f"{i}b", data))]
    groups = find_duplicate_groups(cands)
    assert len(groups) == 8 and peak[0] > 1


def test_scan_stat_is_reused_for_hard_links(tmp_path):
    a = _write(tmp_path / "a", os.urandom(5000))
    os.link(a, tmp_path / "b")
    st = a.stat()
    cands = [FileCandidate(str(p), p.name, st.st_size, st.st_mtime, st.st_dev, st.st_ino)
             for p in (a, tmp_path / "b")]
    assert find_duplicate_groups(cands) == []


# ------------------------------------------------------------- 13. Continuous dispatch, archive cap

def test_one_slow_folder_does_not_hold_up_the_others(tmp_path, monkeypatch):
    for i in range(60):
        _write(tmp_path / f"d{i:02}" / "x.tmp")
    (tmp_path / "slow").mkdir()
    finished = {}
    real = FastScanner._scan_directory_shallow

    def timed(self, path):
        if path.endswith("slow"):
            time.sleep(1.0)
        result = real(self, path)
        finished[os.path.basename(path)] = time.time()
        return result

    monkeypatch.setattr(FastScanner, "_scan_directory_shallow", timed)
    s = FastScanner(ScanOptions(target_path=str(tmp_path), max_workers=4, include_empty_folders=False,
                                leftovers=[], cloud_folders=[]))
    seen_live = []
    s.set_callback(lambda item, stats: seen_live.append(item.path))
    items = s.run_scan()
    assert len(items) == 60 and len(seen_live) == 60
    others = max(t for name, t in finished.items() if name.startswith("d"))
    assert others < finished["slow"]
    assert s.stats.total_dirs_scanned == 61


def test_big_compressed_tars_are_not_listed(tmp_path):
    path = tmp_path / "setup.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo("setup.msi")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"abc"))
    assert archives.inspect_archive(str(path), 1000).is_setup
    assert not archives.inspect_archive(str(path), 65 * 1024 ** 2).is_setup


def test_conda_package_store_is_skipped(tmp_path):
    pkgs = tmp_path / "miniconda3" / "pkgs"
    path = pkgs / "numpy-1.26.tar.bz2"
    with tarfile.open(_write(path, b""), "w:bz2") as tf:
        info = tarfile.TarInfo("setup.msi")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"abc"))
    _write(pkgs / "numpy" / "info" / "x.log")
    assert _classify(path) is None
    assert _classify(pkgs / "numpy" / "info" / "x.log") is None


# ------------------------------------------------------------- Scanner phase and errors (8/9/14)

def test_scan_reports_phases_and_ends_with_done(tmp_path, monkeypatch):
    _write(tmp_path / "a" / "b.tmp")
    (tmp_path / "empty").mkdir()
    s = FastScanner(ScanOptions(target_path=str(tmp_path), include_duplicates=True, leftovers=[], cloud_folders=[]))
    phases = []
    real_empty, real_dups = s._find_empty_folders, s._find_duplicates
    monkeypatch.setattr(s, "_find_empty_folders", lambda roots: phases.append(s.stats.phase) or real_empty(roots))
    monkeypatch.setattr(s, "_find_duplicates", lambda: phases.append(s.stats.phase) or real_dups())
    assert s.stats.phase == "walking"
    s.run_scan()
    assert phases == ["empty_folders", "duplicates"]
    assert s.stats.phase == "done" and s.stats.error == "" and s.stats.is_completed and not s.stats.is_running


def test_scan_error_is_reported_and_scan_ends(tmp_path, monkeypatch):
    _write(tmp_path / "a.tmp")
    s = FastScanner(ScanOptions(target_path=str(tmp_path), leftovers=[], cloud_folders=[]))

    def boom(roots):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(s, "_find_empty_folders", boom)
    s.run_scan()  # Never raises
    assert "disk went away" in s.stats.error
    assert not s.stats.is_running and not s.stats.is_completed and s.stats.phase == "done"


def test_missing_target_still_ends_cleanly(tmp_path):
    s = FastScanner(ScanOptions(target_path=str(tmp_path / "gone"), leftovers=[], cloud_folders=[]))
    assert s.run_scan() == []
    assert not s.stats.is_running and s.stats.is_completed and s.stats.phase == "done"


# ------------------------------------------------------------- 16. Windows junk

def _windows_profile(tmp_path):
    local = tmp_path / "Users" / "me" / "AppData" / "Local"
    win = local / "Microsoft" / "Windows"
    _write(win / "INetCache" / "IE" / "AB12" / "page[1].htm")
    _write(win / "INetCache" / "desktop.ini")
    _write(win / "Explorer" / "thumbcache_256.db")
    _write(win / "Explorer" / "iconcache_256.db")
    _write(win / "WER" / "ReportArchive" / "AppCrash_x" / "Report.wer")
    _write(win / "WER" / "ReportQueue" / "q" / "Report.wer")
    _write(win / "History" / "keep.dat")
    for rel in ("D3DSCache/x", "NVIDIA/DXCache/x", "NVIDIA/GLCache/x", "AMD/DxCache/x", "npm-cache/_cacache/x",
                "pip/Cache/http/x", "NuGet/v3-cache/x", "Yarn/Cache/x", "Google/Chrome/User Data/GrShaderCache/x",
                "Google/Chrome/User Data/ShaderCache/x", "Vivaldi/User Data/Default/Cache/x",
                "Chromium/User Data/Default/Code Cache/x", "Opera Software/Opera Stable/Cache/x"):
        _write(local / rel)
    return local


def test_windows_user_caches_are_offered(tmp_path):
    local = _windows_profile(tmp_path)
    ids = {loc.id for loc in windows_junk_locations({"LOCALAPPDATA": str(local)})}
    assert {"inetcache", "thumbcache", "wer_archive", "wer_queue", "d3dscache", "nvidia_dxcache",
            "nvidia_glcache", "amd_dxcache", "npm_cache", "pip_cache", "nuget_cache", "yarn_cache",
            "chrome:grshadercache", "chrome:shadercache", "vivaldi:default:cache",
            "chromium:default:code_cache", "opera:opera_stable:cache"} <= ids


def test_only_exact_windows_cache_paths_are_unprotected(tmp_path):
    win = "C:/Users/me/AppData/Local/Microsoft/Windows"
    assert not is_system_protected_path(f"{win}/INetCache/IE/x.htm")
    assert not is_system_protected_path(f"{win}/Explorer/thumbcache_32.db")
    assert not is_system_protected_path(f"{win}/WER/ReportArchive/r/Report.wer")
    assert is_system_protected_path(f"{win}/Explorer/iconcache_32.db")
    assert is_system_protected_path(f"{win}/Explorer")
    assert is_system_protected_path(f"{win}/History/x.dat")
    assert is_system_protected_path("C:/Windows/Temp/x.tmp")


def test_windows_cache_scan_lists_only_allowed_files(tmp_path):
    local = _windows_profile(tmp_path)
    locations = windows_junk_locations({"LOCALAPPDATA": str(local)})
    s = FastScanner(ScanOptions(target_path="", junk_locations=locations, scan_junk_locations=True,
                                leftovers=[], cloud_folders=[]))
    names = {i.name for i in s.run_scan()}
    assert {"page[1].htm", "thumbcache_256.db", "D3DSCache", "GrShaderCache"} <= names
    assert "Report.wer" in names
    assert not {"iconcache_256.db", "desktop.ini", "keep.dat"} & names


def test_system_cleanup_hints_are_read_only_listings(tmp_path):
    windir = tmp_path / "Windows"
    _write(windir / "Temp" / "a.tmp", b"12345")
    _write(windir / "SoftwareDistribution" / "Download" / "kb.cab", b"123")
    (tmp_path / "Windows.old").mkdir()
    hints = system_cleanup_hints({"SystemRoot": str(windir), "SystemDrive": str(tmp_path)})
    by_name = {h["name"]: h for h in hints}
    assert set(by_name) == {"Windows temp folder", "Windows Update downloads",
                            "Previous Windows installation (Windows.old)"}
    assert by_name["Windows temp folder"]["size_bytes"] == 5
    assert by_name["Windows Update downloads"]["path"] == str(windir / "SoftwareDistribution" / "Download")
    assert by_name["Previous Windows installation (Windows.old)"]["size_bytes"] == 0
    # Too big to count: the size is unknown rather than wrong
    capped = system_cleanup_hints({"SystemRoot": str(windir), "SystemDrive": str(tmp_path)}, size_limit=0)
    assert {h["name"]: h["size_bytes"] for h in capped}["Windows temp folder"] is None
    assert system_cleanup_hints({}) == []
    assert os.path.exists(windir / "Temp" / "a.tmp")  # Nothing is deleted
