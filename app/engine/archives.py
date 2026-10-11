"""Look inside archives to tell setup downloads from personal archives, without extracting anything.

A .zip is only treated as an installer when its listing is clearly a software package: a setup
program, installer package, disk image or portable app with nothing personal alongside. Archives
holding documents, photos, videos or other personal files are left alone, and so is anything that
cannot be read (password-protected, damaged, or a format without a built-in reader such as .rar).
"""
from __future__ import annotations
import os
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Iterable, List, Optional

from app.config import DISK_IMAGE_EXTENSIONS, INSTALLER_EXTENSIONS

MAX_ENTRIES = 20000                         # Bigger listings are not judged
MAX_COMPRESSED_TAR_BYTES = 512 * 1024 ** 2  # Listing a .tar.gz means decompressing it all
MAX_OTHER_SHARE = 0.10                      # Unrecognized files allowed next to setup files

# Files that by themselves make an archive a software package
SETUP_EXTENSIONS = (INSTALLER_EXTENSIONS | {".mpkg", ".xapk", ".iso", ".img", ".wim", ".esd"}) - {".exe"}
SETUP_SCRIPT_NAMES = {"setup", "install", "installer", "install-sh", "uninstall"}
SETUP_SCRIPT_EXTENSIONS = {".bat", ".cmd", ".ps1", ".sh", ".command", ""}

# Files that come with programs and drivers (never personal on their own)
SUPPORT_EXTENSIONS = {
    ".dll", ".so", ".dylib", ".sys", ".inf", ".cat", ".cab", ".ocx", ".drv", ".mui", ".manifest",
    ".pak", ".dat", ".bin", ".lib", ".a", ".pdb", ".config", ".ini", ".cfg", ".conf", ".json", ".xml",
    ".yaml", ".yml", ".plist", ".nib", ".car", ".strings", ".lproj", ".icns", ".ico", ".cur", ".ttf", ".otf", ".woff", ".woff2", ".js", ".css", ".html", ".htm",
    ".jar", ".asar", ".node", ".pyd", ".pyc", ".qm", ".mo", ".pem", ".crt", ".sig", ".sha256", ".md5",
    ".exe", ".com", ".msp", ".mst", ".tlb", ".ax", ".vbs", ".nupkg", ".ver", ".lic",
}
# Loose images (.png screenshots) and nested archives count as unrecognized, never as program files
SUPPORT_DOC_WORDS = (
    "readme", "read me", "license", "licence", "eula", "copying", "notice", "changelog",
    "install", "setup", "third", "credits", "authors",
)
SUPPORT_DOC_EXTENSIONS = {".txt", ".md", ".rtf", ".pdf", ".htm", ".html", ""}

# Files people make and keep: any of these means the archive is not a setup download
PERSONAL_EXTENSIONS = {
    # documents
    ".doc", ".docx", ".docm", ".odt", ".pages", ".pdf", ".rtf", ".txt", ".md", ".tex", ".epub",
    ".xls", ".xlsx", ".xlsm", ".ods", ".numbers", ".csv", ".ppt", ".pptx", ".odp", ".key",
    ".one", ".pst", ".ost", ".eml", ".msg", ".vcf", ".ics", ".kdbx", ".psd", ".ai", ".indd", ".sketch",
    ".fig", ".xd", ".blend", ".dwg", ".dxf", ".sqlite", ".db", ".accdb", ".mdb", ".qbw", ".gnucash",
    # photos and scans
    ".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff", ".raw", ".cr2", ".cr3", ".nef", ".arw",
    ".dng", ".orf", ".rw2", ".webp", ".avif",
    # audio and video
    ".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".wma", ".aiff", ".mp4", ".mov", ".m4v",
    ".avi", ".mkv", ".wmv", ".3gp", ".mts", ".webm",
}


@dataclass
class ArchiveVerdict:
    """What an archive listing says. is_setup is True only for clearly-software archives."""
    is_setup: bool = False
    readable: bool = True
    summary: str = ""                      # Plain description of what was found inside
    setup_files: List[str] = field(default_factory=list)
    personal_files: List[str] = field(default_factory=list)
    other_count: int = 0
    file_count: int = 0

    @property
    def clean(self) -> bool:
        """Only setup and program files inside, nothing unrecognized."""
        return self.is_setup and self.other_count == 0


def _unreadable(why: str) -> ArchiveVerdict:
    return ArchiveVerdict(readable=False, summary=why)


def _app_bundle(parts: List[str]) -> Optional[str]:
    """Name of the macOS .app bundle a path sits in, if any."""
    for part in parts[:-1]:
        if part.lower().endswith(".app"):
            return part
    return None


def _kind(entry: str) -> str:
    """'setup', 'support', 'personal' or 'other' for one file path inside an archive."""
    parts = [p for p in entry.replace("\\", "/").split("/") if p]
    if not parts:
        return "support"
    name = parts[-1].lower()
    if _app_bundle(parts):
        return "setup" if "/contents/macos/" in entry.lower().replace("\\", "/") else "support"
    if name in {".ds_store", "thumbs.db", "desktop.ini"} or parts[0].lower() == "__macosx":
        return "support"
    stem, suffix = os.path.splitext(name)
    if suffix in SETUP_EXTENSIONS:
        return "setup"
    if suffix == ".exe" and any(w in stem for w in ("setup", "install", "unins", "update")):
        return "setup"
    if suffix in SETUP_SCRIPT_EXTENSIONS and stem in SETUP_SCRIPT_NAMES:
        return "setup"
    if suffix in SUPPORT_DOC_EXTENSIONS and any(w in stem for w in SUPPORT_DOC_WORDS):
        return "support"
    if suffix in PERSONAL_EXTENSIONS:
        return "personal"
    if suffix in SUPPORT_EXTENSIONS:
        return "support"
    return "other"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def judge_listing(names: Iterable[str]) -> ArchiveVerdict:
    """Decide from file names alone whether an archive is a setup package."""
    verdict = ArchiveVerdict()
    portable_exes: List[str] = []
    apps: List[str] = []
    for entry in names:
        if entry.endswith("/"):
            continue
        verdict.file_count += 1
        if verdict.file_count > MAX_ENTRIES:
            return _unreadable(f"more than {MAX_ENTRIES:,} files inside, too many to judge")
        kind = _kind(entry)
        base = entry.replace("\\", "/").rstrip("/").split("/")[-1]
        bundle = _app_bundle([p for p in entry.replace("\\", "/").split("/") if p])
        if bundle and bundle not in apps:
            apps.append(bundle)
        if kind == "setup" and not bundle:
            verdict.setup_files.append(base)
        elif kind == "personal":
            verdict.personal_files.append(base)
        elif kind == "other":
            verdict.other_count += 1
        elif base.lower().endswith(".exe"):
            portable_exes.append(base)

    if verdict.file_count == 0:
        verdict.summary = "empty archive"
        return verdict
    if verdict.personal_files:
        verdict.summary = _found_personal(verdict.personal_files)
        return verdict

    # A program folder (an .exe next to its .dll files) is a portable app
    found = verdict.setup_files + apps
    if not found and portable_exes:
        found = portable_exes
        verdict.setup_files = portable_exes
    if not found:
        verdict.summary = "no setup program or installer package inside"
        return verdict
    if verdict.other_count > max(2, int(verdict.file_count * MAX_OTHER_SHARE)):
        verdict.summary = (f"{_list(found)} inside, but also {_plural(verdict.other_count, 'other file')} "
                           "that are not part of a program")
        return verdict

    verdict.is_setup = True
    verdict.setup_files = found
    verdict.summary = f"{_list(found)} inside"
    if verdict.other_count:
        verdict.summary += f", plus {_plural(verdict.other_count, 'unrecognized file')}"
    return verdict


def _list(names: List[str], limit: int = 2) -> str:
    shown = ", ".join(names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def _found_personal(names: List[str]) -> str:
    return f"holds personal files such as {_list(names)}"


def inspect_archive(path: str, size_bytes: int = 0) -> ArchiveVerdict:
    """Read an archive's file listing (never its contents) and judge it. Never raises."""
    lower = path.lower()
    try:
        if lower.endswith((".zip", ".zipx")):
            with zipfile.ZipFile(path) as zf:
                infos = zf.infolist()
                if any(info.flag_bits & 0x1 for info in infos):
                    return _unreadable("password-protected")
                return judge_listing(info.filename for info in infos)
        if lower.endswith((".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
            if not lower.endswith(".tar") and size_bytes > MAX_COMPRESSED_TAR_BYTES:
                return _unreadable("too large to list without unpacking")
            with tarfile.open(path) as tf:
                names = []
                for member in tf:
                    if len(names) > MAX_ENTRIES:
                        break
                    if member.isfile():
                        names.append(member.name)
                return judge_listing(names)
        if lower.endswith((".gz", ".bz2", ".xz", ".z")):
            # One compressed file: its own name says what it is (ubuntu.img.xz, tool.exe.gz)
            inner = PurePosixPath(os.path.basename(lower)).stem
            suffix = PurePosixPath(inner).suffix
            # A compressed disk image (sdcard.img.gz) is as often a backup as an installer: not judged
            if suffix in DISK_IMAGE_EXTENSIONS:
                return ArchiveVerdict(summary=f"a compressed {suffix} disk image, which may be a backup",
                                      file_count=1)
            if suffix in SETUP_EXTENSIONS:
                return ArchiveVerdict(is_setup=True, summary=f"a compressed {suffix} file",
                                      setup_files=[inner], file_count=1)
            return ArchiveVerdict(summary="a compressed file that is not an installer", file_count=1)
    except (OSError, EOFError, ValueError, zipfile.BadZipFile, tarfile.TarError, RuntimeError, NotImplementedError):
        return _unreadable("could not be read")
    return _unreadable("format JunkZero cannot look inside")
