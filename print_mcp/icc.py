"""ICC profile discovery and description parsing.

Photoshop's print descriptor references a destination profile by its internal
description, not by a file path, so a custom profile has to be read to find that
name. The `desc` (v2) and `mluc` (v4) tag layouts are both handled because
Epson's own profiles are v2 and most modern custom profiles are v4.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

# Search locations, most specific first.
SYSTEM_PROFILE_DIRS = [
    Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "spool" / "drivers" / "color",
    Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "ICC",
]

# Device classes from the ICC spec.
DEVICE_CLASSES = {
    b"scnr": "input",
    b"mntr": "display",
    b"prtr": "output",
    b"link": "link",
    b"spac": "color_space",
    b"abst": "abstract",
    b"nmcl": "named_color",
}

# Color space signatures worth flagging to the caller.
COLOR_SPACES = {
    b"GRAY": "Gray",
    b"RGB ": "RGB",
    b"CMYK": "CMYK",
    b"Lab ": "Lab",
    b"CMY ": "CMY",
    b"2CLR": "Duotone",
    b"XYZ ": "XYZ",
    b"HSV ": "HSV",
    b"HLS ": "HLS",
}


class IccError(ValueError):
    pass


@dataclass
class IccProfile:
    path: Path
    description: str
    device_class: str
    color_space: str
    version: str
    size: int

    def as_dict(self) -> dict[str, str | int]:
        return {
            "description": self.description,
            "path": str(self.path),
            "device_class": self.device_class,
            "color_space": self.color_space,
            "version": self.version,
            "size": self.size,
        }


def _decode_desc_tag(data: bytes) -> str:
    """ICC v2 'desc': ASCII count, ASCII text, then unicode and script variants."""
    if len(data) < 12:
        return ""
    sig = data[:4]
    if sig == b"desc":
        ascii_len = struct.unpack(">I", data[8:12])[0]
        ascii_text = data[12 : 12 + max(0, ascii_len - 1)]
        text = ascii_text.decode("latin-1", errors="replace").rstrip("\x00")
        if text.strip():
            return text.strip()

        # Fall through to the unicode variant.
        uni_off = 12 + ascii_len + 4 + 4
        if len(data) >= uni_off + 8:
            uni_len = struct.unpack(">I", data[uni_off + 4 : uni_off + 8])[0]
            uni = data[uni_off + 8 : uni_off + 8 + uni_len * 2]
            decoded = uni.decode("utf-16-be", errors="replace").rstrip("\x00").strip()
            if decoded:
                return decoded
    return ""


def _decode_mluc_tag(data: bytes) -> str:
    """ICC v4 'mluc': a list of (lang, country, offset, length) UTF-16BE records."""
    if len(data) < 12 or data[:4] != b"mluc":
        return ""
    count = struct.unpack(">I", data[8:12])[0]
    for i in range(min(count, 16)):
        base = 12 + i * 12
        if len(data) < base + 12:
            break
        _lang, _country, length, offset = struct.unpack(">HHII", data[base : base + 12])
        if length == 0 or offset + length > len(data):
            continue
        text = data[offset : offset + length].decode("utf-16-be", errors="replace")
        text = text.replace("\x00", "").strip()
        if text:
            # Prefer English when the profile lists several languages.
            if _lang == b"en":
                return text
            if i == 0:
                first = text
    # Re-scan for English if the first pass found only non-English entries.
    for i in range(min(count, 16)):
        base = 12 + i * 12
        if len(data) < base + 12:
            break
        lang, _country, length, offset = struct.unpack(">HHII", data[base : base + 12])
        if lang == b"en" and length and offset + length <= len(data):
            return data[offset : offset + length].decode("utf-16-be", errors="replace").replace("\x00", "").strip()
    return ""


def read_profile(path: str | os.PathLike[str]) -> IccProfile:
    """Parse the header and description of one .icc/.icm file."""
    p = Path(path)
    raw = p.read_bytes()
    if len(raw) < 132 or raw[36:40] != b"acsp":
        raise IccError(f"{p.name}: not an ICC profile (missing 'acsp' signature)")

    size = struct.unpack(">I", raw[0:4])[0]
    major, minor = raw[8], raw[9]
    version = f"{major}.{minor}.{raw[10]}"
    device_class = DEVICE_CLASSES.get(raw[12:16], raw[12:16].decode("latin-1").strip())
    color_space = COLOR_SPACES.get(raw[16:20], raw[16:20].decode("latin-1").strip())

    # Tag table starts at byte 128: a count, then (signature, offset, size) triples.
    tag_count = struct.unpack(">I", raw[128:132])[0]
    description = ""
    for i in range(min(tag_count, 512)):
        base = 132 + i * 12
        if len(raw) < base + 12:
            break
        sig, offset, tag_size = struct.unpack(">4sII", raw[base : base + 12])
        if sig not in (b"desc", b"mluc"):
            continue
        if offset + tag_size > len(raw):
            continue
        tag = raw[offset : offset + tag_size]
        description = _decode_mluc_tag(tag) or _decode_desc_tag(tag)
        if description:
            break

    return IccProfile(
        path=p,
        description=description or p.stem,
        device_class=device_class,
        color_space=color_space,
        version=version,
        size=size or len(raw),
    )


def list_profiles(extra_dirs: list[str | os.PathLike[str]] | None = None) -> list[IccProfile]:
    """Every readable profile in the system, user and extra directories."""
    dirs: list[Path] = []
    for d in extra_dirs or []:
        dirs.append(Path(d))
    dirs.extend(SYSTEM_PROFILE_DIRS)

    seen: set[Path] = set()
    out: list[IccProfile] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if entry.suffix.lower() not in (".icc", ".icm"):
                continue
            key = entry.resolve()
            if key in seen:
                continue
            seen.add(key)
            try:
                out.append(read_profile(entry))
            except (IccError, OSError):
                # A profile Photoshop rejects is not worth failing the listing over.
                continue
    return sorted(out, key=lambda p: p.description.lower())


def find_profile(
    needle: str,
    extra_dirs: list[str | os.PathLike[str]] | None = None,
) -> list[IccProfile]:
    """Case-insensitive search over profile description and file stem."""
    key = needle.lower()
    return [
        p
        for p in list_profiles(extra_dirs)
        if key in p.description.lower() or key in p.path.stem.lower()
    ]
