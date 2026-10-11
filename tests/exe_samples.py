"""Build tiny Windows programs (PE files) for tests: just enough headers for JunkZero to read."""
import struct


def version_info(**fields):
    """Version-info strings as they sit in a program's resources (UTF-16)."""
    out = b""
    for key, value in fields.items():
        out += key.encode("utf-16-le") + b"\0\0" + b"\0\0" + value.encode("utf-16-le") + b"\0\0"
    return out


def make_exe(path, resources=b"", sections=(".text",), overlay=b""):
    """Write a minimal PE file with the given section names, a .rsrc section and an appended payload."""
    names = list(sections) + [".rsrc"]
    header_size = 0x40 + 4 + 20 + 40 * len(names)
    raw_at = (header_size + 0x1FF) // 0x200 * 0x200
    bodies = [b"\0" * 0x200 for _ in sections] + [resources.ljust(0x200, b"\0")]
    out = bytearray(b"MZ".ljust(0x3C, b"\0") + struct.pack("<I", 0x40))
    out += b"PE\0\0" + struct.pack("<HHIIIHH", 0x8664, len(names), 0, 0, 0, 0, 0x22)
    offset = raw_at
    for name, body in zip(names, bodies):
        out += name.encode().ljust(8, b"\0") + struct.pack("<IIIIIIHHI", len(body), 0x1000, len(body), offset, 0, 0, 0, 0, 0)
        offset += len(body)
    out = out.ljust(raw_at, b"\0")
    for body in bodies:
        out += body
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out) + overlay)
    return path
