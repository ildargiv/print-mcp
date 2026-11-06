"""Check that mirroring preserves resolution and the embedded ICC.

The first print came out of a document reporting 72 dpi while the original is
300 dpi, because the mirrored copy was written without the density tag. That is
not cosmetic: 3543x2480 is A4 at 300 dpi but roughly 88x62 cm at 72, so the sheet
would have been the wrong size. This verifies the fix without printing.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image

from print_mcp.runner import mirrored_copy

SAMPLES = [
    Path(r"Z:\1ПЕЧАТЬ В ПОДВАЛЕ\кро.jpg"),
    Path(r"Z:\1ПЕЧАТЬ В ПОДВАЛЕ\дтф А4 футболка.png"),
]


def physical(im: Image.Image, dpi: tuple[float, float] | None) -> str:
    if not dpi:
        return "unknown"
    return f"{im.size[0] / dpi[0] * 2.54:.1f} x {im.size[1] / dpi[1] * 2.54:.1f} cm"


def main() -> int:
    from PIL import ImageOps

    failures = 0
    workdir = Path(tempfile.mkdtemp(prefix="dpi-check-"))
    try:
        for src in SAMPLES:
            if not src.is_file():
                print(f"skip: {src} not found")
                continue

            # The reference is how Photoshop sees the file, which applies the EXIF
            # orientation. Comparing against the raw stored pixels would flag a
            # correct transpose as a size change.
            with Image.open(src) as raw:
                src_icc = bool(raw.info.get("icc_profile"))
                ref = ImageOps.exif_transpose(raw)
                ref_size = ref.size
                ref_dpi = ref.info.get("dpi") or raw.info.get("dpi")
                raw_size = raw.size
                ref_cm = physical(ref, ref_dpi if ref_dpi else None)
                print(f"SOURCE {src.name}")
                print(f"   stored {raw_size[0]}x{raw_size[1]}  "
                      f"as Photoshop sees it {ref_size[0]}x{ref_size[1]}  "
                      f"dpi={ref_dpi}  icc={src_icc}  -> {ref_cm}")

            copy_path, written = mirrored_copy(src, workdir)
            with Image.open(copy_path) as after:
                copy_dpi = after.info.get("dpi")
                copy_icc = bool(after.info.get("icc_profile"))
                copy_cm = physical(after, copy_dpi if copy_dpi else None)
                leftover = ImageOps.exif_transpose(after).size
                print(f"MIRROR {after.size[0]}x{after.size[1]}  dpi={copy_dpi}  "
                      f"icc={copy_icc}  -> {copy_cm}")

            ok_dpi = bool(ref_dpi) and bool(copy_dpi) and abs(ref_dpi[0] - copy_dpi[0]) < 1
            ok_icc = (not src_icc) or copy_icc
            ok_size = after.size == ref_size
            # The copy must not still carry a rotation tag, or viewers would
            # rotate it a second time.
            ok_no_tag = leftover == after.size
            status = "OK" if (ok_dpi and ok_icc and ok_size and ok_no_tag) else "MISMATCH"
            if status != "OK":
                failures += 1
            print(f"   -> {status}  dpi={ok_dpi} icc={ok_icc} "
                  f"same_pixel_size={ok_size} no_leftover_rotation={ok_no_tag}")
            print()
    finally:
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)

    print("all good" if not failures else f"{failures} mismatch(es)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
