"""Sublimation to mugs on the Epson 1410, driven by PhotoCraft.

PhotoCraft covers the two halves of this job that Photoshop could not reach well:

* **mirroring** - `emulsionDown` flips the page left to right at render time.
  Photoshop could only mirror a temporary file copy (its `flipCanvas` needs a
  `FlipDirection` enum this build does not expose), which meant a PSD could not
  be mirrored at all and a raster file lost its resolution if the density tag
  was not carried across. Mirroring in the renderer touches neither.
* **colour** - `colorHandling` picks who shapes the colour. `noColorManagement`
  is the "colour correction off" case; `photocraftManages` converts into the
  printer profile itself.

What PhotoCraft still cannot do is set the print resolution or the media type:
`file.print` has no parameter for either, so the job silently inherits whatever
the printer's DEVMODE holds. That is why `setup` exists and why the print path
refuses to claim a resolution it did not verify. The Epson driver accepts a
DEVMODE write and then reverts every public field, because media type, quality
and its colour controls live in a private `dmDriverExtra` block with no public
API - so `setup` reports what the driver actually kept instead of assuming.

Usage:
    python tools/pc_sublim.py setup                 # write the driver defaults
    python tools/pc_sublim.py print FILE [FILE...]  # dry run unless --send
    python tools/pc_sublim.py batch FOLDER [--recursive]
    python tools/pc_sublim.py check FILE            # what one file would do

Nothing reaches a printer without `--send`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from print_mcp import printers  # noqa: E402
from print_mcp.runner import collect_files  # noqa: E402

PHOTOCRAFT = r"C:\Program Files\PhotoCraft\photocraft-cli.exe"

PRINTER = "EPSON Stylus Photo 1410 Series"
RESOLUTION = 576
PAPER_SIZE = 9  # A4
ORIENTATION = 1  # portrait
COPIES = 1
PAPER_NAME = "a4"

ICC_DEFAULT = r"C:\Windows\System32\spool\drivers\color\1410 Sublim TKANI photo bumaga.icm"

# Windows paper sizes, for the size hint in the report.
_PAPER_CM = {1: "Letter 21.6x27.9", 5: "Legal 21.6x35.6", 8: "A3 29.7x42", 9: "A4 21x29.7"}


class PrintFailure(RuntimeError):
    pass


# --- PhotoCraft -------------------------------------------------------------


def pc(args: list[str]) -> dict[str, Any] | list[Any]:
    """Run photocraft-cli and return parsed JSON (one object per line)."""
    rows = pc_list(args)
    return rows[0] if len(rows) == 1 else rows


def pc_list(args: list[str]) -> list[Any]:
    """Run photocraft-cli and return every parsed JSON line, always as a list."""
    proc = subprocess.run(
        [PHOTOCRAFT] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        raise PrintFailure(
            f"photocraft-cli failed ({proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[:400]}"
        )
    rows: list[Any] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"raw": line})
    return rows


def doc_info(path: Path) -> dict[str, Any]:
    return pc(["info", str(path), "--compact"])


def physical_cm(width: int, height: int, dpi: float) -> str:
    return f"{width / dpi * 2.54:.1f}x{height / dpi * 2.54:.1f} cm"


# --- driver side ------------------------------------------------------------


def setup(resolution: int = RESOLUTION, copies: int = COPIES) -> dict[str, Any]:
    """Write the printer defaults PhotoCraft will inherit, and report what stuck."""
    result = printers.apply_print_defaults(
        PRINTER,
        copies=copies,
        orientation=ORIENTATION,
        paper_size=PAPER_SIZE,
        resolution=resolution,
        color=True,
        icm="off",  # driver colour management off; the ICC is the only colour authority
    )
    if result.get("icm_method") != printers.ICM_OFF:
        result["warning"] = (
            f"dmICMMethod is still {result['icm_method']} (ICM_OFF is 0), so the driver is "
            "doing its own colour matching. Set the driver's colour controls to "
            "'no colour adjustment' once in the printer properties; the write above is "
            "reverted by Epson on purpose."
        )
    if result.get("print_quality") != resolution:
        result["warning"] = (
            f"resolution is {result.get('print_quality')} dpi, not the requested "
            f"{resolution}. Epson reverted the write, so set it in the driver's UI. "
            "PhotoCraft cannot override it, so every print will come out at this dpi."
        )
    return result


def driver_state() -> dict[str, Any]:
    s = printers.get_devmode(PRINTER).dm.summary()
    s["resolution_matches_profile"] = s["print_quality"] == RESOLUTION
    s["colour_correction_off"] = s["icm_method"] == printers.ICM_OFF
    return s


# --- print path -------------------------------------------------------------


def print_one(
    path: Path,
    *,
    send: bool = False,
    use_icc: bool = False,
    icc: str = ICC_DEFAULT,
    intent: str = "relative",
    copies: int = COPIES,
    pdf_out: Path | None = None,
    fit: bool = True,
) -> dict[str, Any]:
    """Mirror, colour-manage and print one file through PhotoCraft."""
    if not path.is_file():
        raise PrintFailure(f"no such file: {path}")

    info = doc_info(path)
    dpi = float(info.get("resolution") or 0)
    if dpi <= 0:
        raise PrintFailure(
            f"{path.name} carries no resolution, so PhotoCraft would size it from a "
            "72 dpi default and the sheet would come out the wrong size. Tag the file "
            "with a dpi, or set it before printing."
        )

    size_cm = physical_cm(info["width"], info["height"], dpi)

    # The document keeps the resolution it was authored at. Retagging it to the
    # driver's dpi would not make the sheet sharper - it would shrink the print,
    # because a document's physical size is pixels / resolution: 2480x3543 at
    # 300 dpi is A4, at 576 dpi it is 10.9x15.6 cm. The driver rasterises at its
    # own resolution when it renders the placed image, which is the job's output
    # dpi. So nothing is retagged here.
    steps: list[tuple[str, dict[str, Any] | None]] = []

    # The sheet is always A4; which way it lies follows the artwork. A banner is
    # wider than it is tall and goes on A4 landscape, a portrait job on A4
    # portrait. Fixing one orientation for both is what makes a banner print with
    # its edges cropped.
    img_w = info["width"] / dpi * 72
    img_h = info["height"] / dpi * 72
    landscape = img_w > img_h
    sheet_w, sheet_h = (841.89, 595.28) if landscape else (595.28, 841.89)
    overflow = (img_w > sheet_w + 0.5) or (img_h > sheet_h + 0.5)

    params: dict[str, Any] = {
        "printer": PRINTER,
        "copies": copies,
        "paper": PAPER_NAME,
        "orientation": "landscape" if landscape else "portrait",
        "center": True,
        "emulsionDown": True,  # the mirror sublimation needs
    }
    if fit:
        # The artwork is rarely A4-proportioned - a banner, a 40x60 cm sheet or a
        # 21x30 cm design all overflow it. Fitting keeps the whole design on the
        # sheet; leaving it off would crop the edges at scale 100%.
        params["scaleToFit"] = True
    if use_icc:
        params["colorHandling"] = "photocraftManages"
        params["printerProfile"] = icc
        params["intent"] = intent
        params["bpc"] = True
    else:
        params["colorHandling"] = "noColorManagement"

    if pdf_out is not None:
        params["output"] = str(pdf_out)
    if not send:
        params["dryRun"] = True

    steps.append(("file.print", params))

    args = ["run", str(path)]
    for cmd, prm in steps:
        args += ["--cmd", cmd]
        if prm is not None:
            args += ["--params", json.dumps(prm)]
    results = pc_list(args)

    last = results[-1].get("result") or {} if isinstance(results[-1], dict) else {}
    return {
        "file": str(path),
        "pixels": f"{info['width']}x{info['height']}",
        "document_dpi": round(dpi, 1),
        "physical": size_cm,
        "sheet": "A4 " + ("landscape" if landscape else "portrait")
                 + f" {sheet_w:.0f}x{sheet_h:.0f} pt",
        "placement": f"{img_w:.0f}x{img_h:.0f} pt"
                     + (" (fits, nothing cropped)" if fit else " (scale 100%)"),
        "overflows_sheet": overflow,
        "mirror": "emulsionDown (renderer, no temp file)",
        "colour": params["colorHandling"] + (f" + {icc}" if use_icc else " (correction off)"),
        "paper": last.get("paper"),
        "image_rect": [round(v, 1) for v in last.get("imageRect", [])],
        "scale": round(float(last.get("scale", 0)), 2),
        "pdf": last.get("pdf"),
        "sent": bool(last.get("sent")),
        "spooler": last.get("command"),
    }


def batch(
    paths: list[str],
    *,
    send: bool = False,
    use_icc: bool = False,
    recursive: bool = False,
    delay: float = 2.0,
    pdf_dir: Path | None = None,
    fit: bool = True,
) -> dict[str, Any]:
    state = driver_state()
    files: list[Path] = []
    for p in paths:
        files.extend(collect_files(p, recursive=recursive))

    results: list[dict[str, Any]] = []
    for index, f in enumerate(files):
        out = None
        if pdf_dir is not None:
            pdf_dir.mkdir(parents=True, exist_ok=True)
            out = pdf_dir / (f.stem + ".pdf")
        try:
            results.append(
                print_one(f, send=send, use_icc=use_icc, pdf_out=out, fit=fit)
            )
        except PrintFailure as exc:
            results.append({"file": str(f), "sent": False, "error": str(exc)})
        if delay and index < len(files) - 1 and send:
            time.sleep(delay)

    return {
        "driver": state,
        "requested": len(files),
        "printed": sum(1 for r in results if r.get("sent")),
        "dry_run": not send,
        "results": results,
    }


# --- cli --------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="1410 mug sublimation via PhotoCraft")
    ap.add_argument("command", choices=["setup", "print", "batch", "check"])
    ap.add_argument("targets", nargs="*", help="files or folders")
    ap.add_argument("--send", action="store_true", help="really print (default: dry run)")
    ap.add_argument("--icc", action="store_true", help="let PhotoCraft use the printer profile")
    ap.add_argument("--icc-path", default=ICC_DEFAULT)
    ap.add_argument("--intent", default="relative")
    ap.add_argument("--copies", type=int, default=COPIES)
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--no-fit", action="store_true",
                    help="print at scale 100%% and let the edges crop instead of fitting")
    ap.add_argument("--pdf-dir", type=Path, help="render to PDF instead of the queue")
    ap.add_argument("--resolution", type=int, default=RESOLUTION)
    args = ap.parse_args()

    if args.command == "setup":
        result = setup(resolution=args.resolution, copies=args.copies)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if result.get("warning") or result.get("ignored") else 0

    if args.command in ("print", "batch") and not args.targets:
        ap.error(f"{args.command} needs at least one file or folder")

    if args.command == "check":
        print(json.dumps(driver_state(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "print":
        files: list[str] = []
        for t in args.targets:
            p = Path(t)
            files.extend([str(f) for f in collect_files(p)] if p.is_dir() else [t])
        out = batch(files, send=args.send, use_icc=args.icc,
                    pdf_dir=args.pdf_dir, delay=0.0, fit=not args.no_fit)
    else:
        out = batch(args.targets, send=args.send, use_icc=args.icc,
                    recursive=args.recursive, pdf_dir=args.pdf_dir,
                    fit=not args.no_fit)

    print(json.dumps(out, indent=2, ensure_ascii=False))

    st = out["driver"]
    notes = []
    if not st["resolution_matches_profile"]:
        notes.append(f"WARNING: driver is at {st['print_quality']} dpi, profile wants "
                     f"{RESOLUTION}. Run 'setup' or set it in the driver UI.")
    if not st["colour_correction_off"]:
        notes.append("WARNING: driver colour correction is still ON (dmICMMethod "
                     f"{st['icm_method']}); set 'no colour adjustment' in the driver UI.")
    if not args.send:
        notes.append("dry run - nothing was sent. Add --send to print.")
    for r in out.get("results", []):
        if r.get("overflows_sheet") and args.no_fit:
            notes.append(f"NOTE: {Path(r['file']).name} is "
                         f"{r['physical']}, larger than {r['sheet']} - the edges are "
                         "cropped. Drop --no-fit to place the whole design.")
    for n in notes:
        print(n, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())