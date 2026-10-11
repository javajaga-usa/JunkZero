"""Tell setup programs apart from other .exe files by looking inside them.

Installer builders leave clear fingerprints in the programs they make: Inno Setup, NSIS,
InstallShield and others write their name into the file, WiX bundles carry a ".wixburn"
section, and most installers describe themselves as "Setup" or "Installer" in their version
info. Portable apps, tools and games carry none of these, so they are not treated as installers.

Only the PE headers, the resource section and the start of any appended payload are read
(a few MB at most), never the whole file.
"""
from __future__ import annotations
import ntpath
import os
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

HEAD_BYTES = 2 * 1024 ** 2      # Installer stubs are small; their payload is appended at the end
RSRC_BYTES = 4 * 1024 ** 2      # Most of a version-info / manifest resource section
OVERLAY_BYTES = 64 * 1024       # Start of the appended payload (NSIS and Inno headers live here)

# Text the installer builders leave in the programs they make
BUILDER_MARKERS: List[Tuple[bytes, str]] = [
    (b"JR.Inno.Setup", "Inno Setup"),
    (b"Inno Setup Setup Data", "Inno Setup"),
    (b"NullsoftInst", "NSIS (Nullsoft)"),
    (b"Nullsoft.NSIS", "NSIS (Nullsoft)"),
    (b"InstallShield", "InstallShield"),
    (b"Advanced Installer", "Advanced Installer"),
    (b"InstallAware", "InstallAware"),
    (b"Setup Factory", "Setup Factory"),
    (b"Wise Installation", "Wise Installer"),
    (b"Smart Install Maker", "Smart Install Maker"),
    (b"InstallBuilder", "InstallBuilder"),
    (b"SquirrelSetup", "Squirrel"),
    (b"Squirrel.Windows", "Squirrel"),
]
VERSION_KEYS = ("FileDescription", "ProductName", "OriginalFilename", "InternalName")
INSTALLER_WORDS = ("setup", "installer", "install", "bootstrapper", "redistributable", "redist")
UNINSTALLER_WORDS = ("uninst", "unins0")
NAME_WORDS = ("setup", "install")


@dataclass
class ExeVerdict:
    """is_installer: a builder fingerprint or installer version info was found."""
    is_installer: bool = False
    is_uninstaller: bool = False
    evidence: str = ""


def _read(f, offset: int, size: int) -> bytes:
    if size <= 0 or offset < 0:
        return b""
    f.seek(offset)
    return f.read(size)


def _sections(head: bytes) -> Optional[List[Tuple[str, int, int]]]:
    """(name, raw offset, raw size) of each PE section, or None if this is not a Windows program."""
    if len(head) < 0x40 or head[:2] != b"MZ":
        return None
    pe = struct.unpack_from("<I", head, 0x3C)[0]
    if pe + 24 > len(head) or head[pe:pe + 4] != b"PE\0\0":
        return None
    count, = struct.unpack_from("<H", head, pe + 6)
    opt_size, = struct.unpack_from("<H", head, pe + 20)
    table = pe + 24 + opt_size
    sections = []
    for i in range(min(count, 96)):
        at = table + 40 * i
        if at + 40 > len(head):
            break
        name = head[at:at + 8].rstrip(b"\0").decode("latin-1")
        raw_size, raw_ptr = struct.unpack_from("<II", head, at + 16)
        sections.append((name, raw_ptr, raw_size))
    return sections


def _version_strings(data: bytes) -> List[str]:
    """Values of the version-info fields that describe the program (UTF-16 in the resources)."""
    values = []
    for key in VERSION_KEYS:
        tag = key.encode("utf-16-le") + b"\0\0"
        at = data.find(tag)
        if at < 0:
            continue
        pos = at + len(tag)
        while data[pos:pos + 2] == b"\0\0" and pos < at + len(tag) + 8:
            pos += 2
        end = pos
        while end < len(data) and end < pos + 400 and data[end:end + 2] != b"\0\0":
            end += 2
        value = data[pos:end].decode("utf-16-le", "ignore").strip()
        if value:
            values.append(value)
    return values


def _has(data: bytes, marker: bytes) -> bool:
    return marker in data or marker.decode().encode("utf-16-le") in data


def inspect_exe(path: str) -> ExeVerdict:
    """Look for installer fingerprints in a Windows program. Never raises."""
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_BYTES)
            sections = _sections(head)
            if sections is None:
                return ExeVerdict()
            names = {s[0].lower() for s in sections}
            rsrc = next((s for s in sections if s[0].lower() == ".rsrc"), None)
            resources = b""
            if rsrc and rsrc[1] + rsrc[2] > len(head):
                resources = _read(f, rsrc[1], min(rsrc[2], RSRC_BYTES))
            overlay_at = max((s[1] + s[2] for s in sections), default=0)
            overlay = _read(f, overlay_at, OVERLAY_BYTES) if overlay_at >= len(head) else b""
    except (OSError, struct.error, ValueError):
        return ExeVerdict()

    data = head + resources + overlay
    described = _version_strings(data)
    lowered = " ".join(described).lower()
    if any(w in lowered for w in UNINSTALLER_WORDS):
        return ExeVerdict(is_uninstaller=True, evidence="it is an uninstaller")

    if ".wixburn" in names:
        return ExeVerdict(True, evidence="WiX installer bundle")
    for marker, builder in BUILDER_MARKERS:
        if _has(data, marker):
            return ExeVerdict(True, evidence=f"built with {builder}")
    for value in described:
        if any(w in value.lower() for w in INSTALLER_WORDS):
            return ExeVerdict(True, evidence=f'describes itself as "{value[:80]}"')
    return ExeVerdict()


def named_like_installer(name: str) -> bool:
    lower = name.lower()
    return any(w in lower for w in NAME_WORDS) and not any(w in lower for w in UNINSTALLER_WORDS)


def is_uninstaller_name(name: str) -> bool:
    return any(w in name.lower() for w in UNINSTALLER_WORDS)


def inside_installed_program(path: str) -> bool:
    """True for programs that are part of an installed or unpacked app (never junk on their own).

    Program Files and AppData hold installed apps and their updaters (Squirrel's Update.exe),
    except the temp folder. A folder with .dll files next to the exe is a program folder.
    """
    parts = [p.lower() for p in ntpath.normpath(path).replace("\\", "/").split("/")[:-1]]
    in_temp = any(p in {"temp", "tmp"} for p in parts)
    if not in_temp and any(p in {"appdata", "program files", "program files (x86)", "programdata"} for p in parts):
        return True
    try:
        with os.scandir(os.path.dirname(path) or ".") as it:
            return any(e.name.lower().endswith(".dll") for e in it)
    except OSError:
        return False
