"""Print runner: named profiles, single files, folders, and reporting.

A `PrintProfile` is the saved answer to "how does this job print" - printer, ICC,
media, resolution, mirroring. The colour chain is split across two processes
because Photoshop only exposes half of it:

* the printer profile, rendering intent and gamut compensation are saved in
  Photoshop's Print dialog and cannot be scripted in this build - see `jsx.py`
  for the measurements behind that;
* media type, resolution, copies and the driver's `dmICMMethod` are set from here
  through the DEVMODE, and `dmICMMethod = ICM_OFF` is what actually disables the
  driver's automatic colour correction;
* mirroring, which sublimation needs, is applied to a temporary copy of the file
  because Photoshop's `flipCanvas` cannot be called without an enum this host does
  not expose.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Iterable

from . import icc, printers
from .jsx import PrintJob, build_print_job_js
from .photoshop import Photoshop, PhotoshopBusy

# Extensions Photoshop can open for printing.
PRINTABLE_EXTENSIONS = {".psd", ".psb", ".tif", ".tiff", ".png", ".jpg", ".jpeg"}

#: Extensions that can be mirrored without going through Photoshop. Photoshop
#: cannot flip, and a PSD cannot be mirrored as a flat image, so those are
#: reported as unsupported rather than silently printed the wrong way round.
MIRRORABLE_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


class PrintError(RuntimeError):
    pass


#: Prefixes of `PrintProfile.validate()` notes that describe rather than block.
#: Both halves of the colour chain are split across two programs, so "the ICC lives
#: in Photoshop's dialog" and "mirroring happens on a file copy" are expected, not
#: failures. Anything else - a printer that is not installed, an ICC that cannot be
#: resolved - stops the run.
NON_BLOCKING = ("note:", "mirror is on")


def blocking(problems: list[str]) -> list[str]:
    """The problems worth refusing to print over."""
    return [p for p in problems if not p.startswith(NON_BLOCKING)]


@dataclass
class PrintProfile:
    """A saved set of print settings."""

    name: str
    printer: str
    """Windows printer whose DEVMODE and queue are managed. Photoshop prints to
    whichever printer its Print dialog has selected, so this must match."""

    icc: str | None = None
    """Destination ICC, recorded for the report. It is the profile set in
    Photoshop's Print dialog; `print_validate_profile` checks that a profile with
    this name is installed so a typo is caught before a batch runs."""

    icc_profile_dirs: list[str] = field(default_factory=list)
    resolution: int | None = None
    media_type: int | None = None
    paper_size: int | None = None
    orientation: int | None = None
    copies: int = 1
    color: bool = True
    mirror: bool = True
    """True for sublimation, where the transfer is applied face down. False for
    direct-to-garment and photo paper."""

    delay_seconds: float = 0.0
    """Pause between files, so a slow Epson queue is not overrun."""

    stop_on_error: bool = False
    recursive: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def resolved_icc(self) -> icc.IccProfile | None:
        if not self.icc:
            return None
        direct = Path(self.icc)
        if direct.is_file():
            return icc.read_profile(direct)
        matches = icc.find_profile(self.icc, self.icc_profile_dirs)
        if not matches:
            raise PrintError(
                f"profile {self.name!r}: no ICC matching {self.icc!r}. "
                f"Call print_find_icc to see what is installed."
            )
        for wanted in ("output", "link"):
            for profile in matches:
                if profile.device_class == wanted:
                    return profile
        return matches[0]

    def validate(self) -> list[str]:
        """Non-fatal problems worth reporting before a run starts."""
        problems: list[str] = []
        known = {p.name for p in printers.list_printers()}
        if self.printer not in known:
            problems.append(
                f"printer {self.printer!r} is not installed; "
                f"available: {', '.join(sorted(known)) or 'none'}"
            )
        if self.icc:
            try:
                resolved = self.resolved_icc()
                problems.append(
                    f"note: {resolved.description!r} is verified installed, but Photoshop "
                    "reads the profile from its own Print dialog, which cannot be set "
                    "from a script. Make sure the dialog is set to it."
                )
            except PrintError as exc:
                problems.append(str(exc))
        if self.mirror:
            problems.append(
                "mirror is on: raster files are printed from a mirrored temporary copy "
                "because Photoshop cannot flip. PSD/PSB cannot be mirrored this way."
            )
        return problems


@dataclass
class PrintResult:
    file: str
    ok: bool
    printed_path: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_files(path: str, recursive: bool = False) -> list[Path]:
    """Printable files from a folder or a single file, in a stable order."""
    p = Path(path)
    if p.is_file():
        return [p] if p.suffix.lower() in PRINTABLE_EXTENSIONS else []
    if not p.is_dir():
        raise PrintError(f"no such file or folder: {p}")

    candidates: Iterable[Path] = sorted(p.rglob("*")) if recursive else sorted(p.iterdir())
    return [
        f
        for f in candidates
        if f.is_file()
        and f.suffix.lower() in PRINTABLE_EXTENSIONS
        and not f.name.startswith(("~", "."))
    ]


def _read_dpi(img: Any) -> tuple[int, int] | None:
    """Physical resolution of an opened image, whichever tag carries it.

    JFIF needs care: `jfif_density` is a pair of numbers that mean pixels per inch
    only when `jfif_unit` says so, and Pillow hands over `(1, 1)` for a plain JPEG
    that carries no resolution at all. Reading that as 1 dpi would write a 1 dpi
    tag onto the mirrored copy and blow the sheet size up by 300 times.
    """
    info = getattr(img, "info", {}) or {}

    def pair(value: Any) -> tuple[int, int] | None:
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            return None
        x, y = value
        if not x or not y or x <= 0 or y <= 0:
            return None
        return int(round(x)), int(round(y))

    for key in ("dpi", "resolution"):
        found = pair(info.get(key))
        if found:
            return found

    unit = info.get("jfif_unit", info.get("jfif_density_units"))
    if unit == 1:  # dots per inch
        found = pair(info.get("jfif_density"))
        if found:
            return found
    # unit 0 is "aspect ratio only" and unit 2 is "aspect ratio only" as well, so
    # neither carries a resolution. Returning None makes the caller leave the tag
    # off rather than invent one.
    return None


def mirrored_copy(path: Path, workdir: Path) -> tuple[Path, tuple[int, int] | None]:
    """Write a horizontally mirrored copy of a raster file.

    Returns the copy and the resolution it was written with.

    The original is never touched. Photoshop's `flipCanvas` needs the
    `FlipDirection` enum, which this Photoshop build does not expose, so the flip
    happens here.

    Resolution and the embedded ICC have to be carried across explicitly. Losing
    the resolution is not cosmetic: a 3543x2480 image is A4 at 300 dpi but about
    88x62 cm at the 72 dpi that image libraries default to, and the print comes out
    the wrong physical size.
    """
    from PIL import Image, ImageOps

    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / f"m_{path.name}"
    with Image.open(path) as img:
        # EXIF orientation is applied first so the mirror is about pixels only.
        img = ImageOps.exif_transpose(img)
        dpi = _read_dpi(img)
        icc = img.info.get("icc_profile")
        flipped = ImageOps.mirror(img)

        suffix = path.suffix.lower()
        save_kwargs: dict[str, Any] = {}
        if suffix in (".jpg", ".jpeg"):
            save_kwargs["quality"] = 100
            save_kwargs["subsampling"] = 0  # no chroma loss on a transfer print
            save_kwargs["progressive"] = False
        elif suffix == ".png":
            pass
        elif suffix in (".tif", ".tiff"):
            save_kwargs["compression"] = "tiff_lzw"

        if dpi:
            save_kwargs["dpi"] = dpi
        if icc:
            save_kwargs["icc_profile"] = icc

        flipped.save(target, **save_kwargs)

    return target, dpi


class PrintRunner:
    """Applies a profile to the printer and to Photoshop, then runs jobs."""

    def __init__(self, photoshop: Photoshop | None = None) -> None:
        self.ps = photoshop or Photoshop()

    # -- driver side -------------------------------------------------------

    def apply_driver(self, profile: PrintProfile) -> dict[str, Any]:
        """Write the printer default DEVMODE and report what the driver kept.

        This is best effort by necessity, not by design: Epson's drivers accept
        the write and then revert every public field, because media type, quality
        and the colour controls live in a private block with no public API. The
        returned `ignored` list is what matters - when it is non-empty those
        settings have to be made once in the driver's own UI.
        """
        result = printers.apply_print_defaults(
            profile.printer,
            copies=profile.copies,
            orientation=profile.orientation,
            paper_size=profile.paper_size,
            resolution=profile.resolution,
            media_type=profile.media_type,
            color=profile.color,
            icm="off",
        )
        result["requested_policy"] = "driver colour management off"
        if result["icm_method"] != printers.ICM_OFF:
            result["warning"] = (
                "dmICMMethod is still "
                f"{result['icm_method']}, so the driver is doing its own colour matching "
                "and Photoshop's profile is not the last word. Set the driver's colour "
                "controls to 'no colour adjustment' once in the printer properties."
            )
        if result["ignored"]:
            result["warning_ignored"] = (
                "the driver ignored some settings; they have to be set in the driver's "
                "own UI: " + "; ".join(result["ignored"])
            )
        return result

    # -- one file ----------------------------------------------------------

    def print_file(self, profile: PrintProfile, file_path: str) -> PrintResult:
        source = Path(file_path)
        printed: Path | None = None
        workdir: Path | None = None
        written_dpi: tuple[int, int] | None = None

        if profile.mirror:
            if source.suffix.lower() not in MIRRORABLE_EXTENSIONS:
                return PrintResult(
                    file=str(source),
                    ok=False,
                    error=(
                        f"{source.suffix} cannot be mirrored automatically: Photoshop's "
                        f"flipCanvas is unavailable in this build and a layered file "
                        f"cannot be mirrored as a flat image. Pre-mirror the file, or "
                        f"set mirror=false if the printer or RIP already mirrors."
                    ),
                )
            workdir = Path(tempfile.mkdtemp(prefix="print-mcp-"))
            printed, written_dpi = mirrored_copy(source, workdir)
        else:
            printed = source

        job = PrintJob(file_path=str(printed), printer=profile.printer)
        try:
            report = self.ps.run_js_json(build_print_job_js(job))
        except PhotoshopBusy as exc:
            return PrintResult(file=str(source), ok=False, error=f"Photoshop busy: {exc}")
        except Exception as exc:  # noqa: BLE001 - reported to the caller as a result
            return PrintResult(
                file=str(source), ok=False, error=f"{type(exc).__name__}: {exc}"
            )
        finally:
            if workdir is not None:
                shutil.rmtree(workdir, ignore_errors=True)

        if not isinstance(report, dict):
            return PrintResult(
                file=str(source), ok=False, error=f"unexpected script output: {report!r}"
            )
        ok = bool(report.get("ok"))

        detail = {k: v for k, v in report.items() if k not in ("ok", "error")}
        detail["mirrored"] = profile.mirror
        if profile.mirror:
            detail["mirrored_copy_dpi"] = list(written_dpi) if written_dpi else None
            # The mirrored copy is what Photoshop opened, so a resolution that
            # differs from the original means the print will be the wrong size.
            doc = detail.get("document")
            if isinstance(doc, dict) and written_dpi:
                try:
                    opened = round(float(doc.get("resolution", 0)))
                except (TypeError, ValueError):
                    opened = 0
                if opened and abs(opened - written_dpi[0]) > 1:
                    detail["warning"] = (
                        f"the mirrored copy was written at {written_dpi[0]} dpi but "
                        f"Photoshop reports {opened} dpi, so the print size will be wrong"
                    )

        return PrintResult(
            file=str(source),
            ok=ok,
            printed_path=str(printed) if printed != source else None,
            detail=detail,
            error=None if ok else f"{report.get('stage', '?')}: {report.get('error')}",
        )

    # -- batch -------------------------------------------------------------

    def print_batch(
        self,
        profile: PrintProfile,
        paths: list[str],
        apply_driver: bool = True,
        unmirrorable: list[str] | None = None,
    ) -> dict[str, Any]:
        """Print files in order, reporting each one.

        Driver settings are written once rather than per file: a single
        DocumentProperties round trip per sheet would be slow, and the values are
        identical for the whole run.

        Files that cannot be mirrored with the profile's settings are reported and
        not sent to Photoshop. Printing them anyway would put them on the paper the
        wrong way round, which for sublimation means a transfer that cannot be
        applied at all.
        """
        started = time.time()
        problems = profile.validate()
        stop = blocking(problems)
        if stop:
            return {
                "ok": False,
                "stage": "profile",
                "error": "; ".join(stop),
                "blocking": stop,
                "profile": profile.name,
                "notes": problems,
            }
        results: list[PrintResult] = []
        driver: dict[str, Any] | None = None

        if apply_driver:
            try:
                driver = self.apply_driver(profile)
            except printers.PrinterError as exc:
                return {
                    "ok": False,
                    "stage": "driver",
                    "error": str(exc),
                    "profile": profile.name,
                }

        known = set(paths)
        skipped = list(unmirrorable or []) if profile.mirror else []
        queue = [p for p in paths if p not in set(skipped)]
        for index, path in enumerate(queue):
            result = self.print_file(profile, path)
            results.append(result)
            if not result.ok and profile.stop_on_error:
                break
            if profile.delay_seconds and index < len(queue) - 1:
                time.sleep(profile.delay_seconds)

        failed = [r for r in results if not r.ok]
        not_attempted = [p for p in queue[len(results) :] if profile.stop_on_error and failed]
        return {
            "ok": not failed and not skipped,
            "profile": profile.name,
            "printer": profile.printer,
            "mirror": profile.mirror,
            "requested": len(known),
            "total": len(results),
            "printed": sum(1 for r in results if r.ok),
            "failed": len(failed),
            "skipped_unmirrorable": skipped,
            "not_attempted": not_attempted,
            "duration_seconds": round(time.time() - started, 2),
            "notes": problems,
            "driver": driver,
            "results": [r.as_dict() for r in results],
        }


# --- profile storage --------------------------------------------------------

PROFILE_DIR = Path.home() / ".print-mcp" / "profiles"


def _profile_path(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip() or "default"
    return PROFILE_DIR / f"{safe}.json"


def save_profile(profile: PrintProfile) -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    path = _profile_path(profile.name)
    # Sanitizing the file name is lossy: "a/b" and "ab" both become "ab.json", and
    # a name made only of invalid characters becomes "default.json". Without this
    # check one profile silently overwrites another.
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
        stored = existing.get("name")
        if stored != profile.name:
            raise PrintError(
                f"profile {profile.name!r} would be written to {path.name}, which already "
                f"holds a profile named {stored!r}. The file name is derived from the "
                "profile name, so rename one of them."
            )
    path.write_text(json.dumps(profile.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_profile(name: str) -> PrintProfile:
    path = _profile_path(name)
    if not path.is_file():
        available = [p.stem for p in PROFILE_DIR.glob("*.json")] if PROFILE_DIR.is_dir() else []
        raise PrintError(
            f"no profile named {name!r}. Saved profiles: {', '.join(available) or 'none'}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    known = set(PrintProfile.__dataclass_fields__)
    return PrintProfile(**{k: v for k, v in data.items() if k in known})


def list_profiles() -> list[PrintProfile]:
    if not PROFILE_DIR.is_dir():
        return []
    out: list[PrintProfile] = []
    for path in sorted(PROFILE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = set(PrintProfile.__dataclass_fields__)
            out.append(PrintProfile(**{k: v for k, v in data.items() if k in known}))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def delete_profile(name: str) -> bool:
    path = _profile_path(name)
    if path.is_file():
        path.unlink()
        return True
    return False
