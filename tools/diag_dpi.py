"""Compare the original's resolution metadata against the mirrored copy.

The mirrored copy is what Photoshop actually opened, and the print came out of a
document reporting 72 dpi while the original reports 300. A 2480x3543 image is
A4 at 300 dpi but roughly 87x124 cm at 72, so if the mirrored copy really lost its
density the print would be the wrong physical size. This confirms it before a fix
is written.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ORIGINAL = Path(r"Z:\1ПЕЧАТЬ В ПОДВАЛЕ\кро.jpg")
MIRRORED = Path(r"C:\Users\Pod\AppData\Local\Temp\print-mcp-x60x2xu2\m_кро.jpg")


def describe(label: str, path: Path) -> None:
    if not path.is_file():
        print(f"{label}: MISSING ({path})")
        return
    with Image.open(path) as im:
        dpi = im.info.get("dpi")
        print(f"{label}")
        print(f"   path        {path}")
        print(f"   pixels      {im.size[0]} x {im.size[1]}")
        print(f"   dpi         {dpi}")
        print(f"   icc         {'yes' if im.info.get('icc_profile') else 'no'}")
        for candidate in ([dpi] if isinstance(dpi, (tuple, list)) else [dpi, None]):
            if candidate:
                cm_w = im.size[0] / candidate * 2.54
                cm_h = im.size[1] / candidate * 2.54
                print(f"   at {candidate} dpi -> {cm_w:.1f} x {cm_h:.1f} cm")


def main() -> int:
    describe("ORIGINAL", ORIGINAL)
    print()
    describe("MIRRORED COPY (what Photoshop opened)", MIRRORED)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
