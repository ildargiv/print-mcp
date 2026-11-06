"""Printer and DEVMODE access.

The print driver owns the half of colour management that Photoshop cannot reach:
Media Type, Print Quality and the driver's own colour controls live in the
DEVMODE, not in the Photoshop print descriptor. Photoshop reads the printer's
default DEVMODE when it prints, so setting the default here is how a print job
inherits a media type, a resolution and a colour-management policy.

`dmICMMethod` is the switch that turns the driver's colour management off
(ICM_OFF). With it off and Photoshop converting into the destination profile,
the ICC profile is the only thing shaping the colour.

Layout note: DEVMODEW is read through a ctypes structure rather than raw offsets
because the tail fields are easy to misplace. The correct offsets are
dmICMMethod=188, dmICMIntent=192, dmMediaType=196, dmDitherType=200.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

# --- win32 constants --------------------------------------------------------
#
# DEVMODE reuses the same dmFields bit positions for two different flag sets, so
# DM_ICMMETHOD is numerically identical to DM_ORIENTATION and DM_MEDIATYPE to
# DM_PAPERLENGTH. Setting one therefore advertises the other. The names are kept
# separate here for readability, but they must never be compared against each
# other to decide what a driver supports.

DM_ORIENTATION = 0x00000001
DM_PAPERSIZE = 0x00000002
DM_PAPERLENGTH = 0x00000004
DM_PAPERWIDTH = 0x00000008
DM_SCALE = 0x00000010
DM_COPIES = 0x00000020
DM_DEFAULTSOURCE = 0x00000040
DM_PRINTQUALITY = 0x00000080
DM_COLOR = 0x00000100
DM_DUPLEX = 0x00000200
DM_YRESOLUTION = 0x00000400
DM_TTOPTION = 0x00000800
DM_COLLATE = 0x00001000
DM_FORMNAME = 0x00010000

# Overlapping aliases, see the note above.
DM_ICMMETHOD = 0x00000001
DM_ICMINTENT = 0x00000002
DM_MEDIATYPE = 0x00000004
DM_DITHER = 0x00000008

# DEVMODE.dmICMMethod values (winuser.h)
ICM_OFF = 0
ICM_ON = 1
ICM_DEVCONST = 2
ICM_DEVMATCH = 3
ICM_HANDLES = 4
ICM_BW = 5

ICM_INTENT_PERCEPTUAL = 0
ICM_INTENT_RELATIVE = 1
ICM_INTENT_SATURATION = 2
ICM_INTENT_ABSOLUTE = 3

# DocumentProperties modes
DM_OUT_BUFFER = 0x00000001
DM_IN_BUFFER = 0x00000002
DM_PERSIST = 0x00000004
DM_USER = 0x00000100

# dmColor
DMCOLOR_MONOCHROME = 1
DMCOLOR_COLOR = 2

CCHDEVICENAME = 32
CCHFORMNAME = 32


class DEVMODEW(ctypes.Structure):
    """Windows DEVMODEW. Field order and padding come from wingdi.h."""

    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * CCHDEVICENAME),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * CCHFORMNAME),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningLength", wintypes.DWORD),
    ]

    def summary(self) -> dict[str, Any]:
        f = self.dmFields
        return {
            "device": self.dmDeviceName,
            "spec_version": f"{(self.dmSpecVersion << 8) | (self.dmDriverVersion & 0xFF):#06x}",
            "dm_size": self.dmSize,
            "driver_extra": self.dmDriverExtra,
            "dm_fields": f"{f:#010x}",
            "orientation": self.dmOrientation,
            "paper_size": self.dmPaperSize,
            "paper_length_tenths_mm": self.dmPaperLength,
            "paper_width_tenths_mm": self.dmPaperWidth,
            "scale": self.dmScale,
            "copies": self.dmCopies,
            "default_source": self.dmDefaultSource,
            # Epson's driver stores the print resolution here, not in dmYResolution.
            "print_quality": self.dmPrintQuality,
            "color": self.dmColor,
            "duplex": self.dmDuplex,
            "y_resolution": self.dmYResolution,
            "collate": self.dmCollate,
            "form_name": self.dmFormName,
            "icm_method": self.dmICMMethod,
            "icm_intent": self.dmICMIntent,
            "media_type": self.dmMediaType,
            "dither_type": self.dmDitherType,
        }


winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)

winspool.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID]
winspool.OpenPrinterW.restype = wintypes.BOOL
winspool.ClosePrinter.argtypes = [wintypes.HANDLE]
winspool.ClosePrinter.restype = wintypes.BOOL
winspool.DocumentPropertiesW.argtypes = [
    wintypes.HWND,
    wintypes.HANDLE,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
]
winspool.DocumentPropertiesW.restype = ctypes.c_int

# DeviceCapabilities is the only public route to a driver's private media/quality
# lists (DC_MEDIATYPE, DC_PAPERS, DC_PAPERNAMES, DC_PRINTQUALITY).
winspool.DeviceCapabilitiesW.argtypes = [
    wintypes.HDC,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.WORD,
]
winspool.DeviceCapabilitiesW.restype = ctypes.c_long

DC_PAPERS = 1
DC_PAPERSIZE = 2
DC_PAPERNAMES = 16
DC_MEDIATYPE = 4
DC_MEDIATYPENAMES = 36
DC_PRINTQUALITY = 18
DC_PRINTQUALITYNAME = 40
DC_ORIENTATION = 1


class PrinterError(RuntimeError):
    pass


@dataclass
class PrinterInfo:
    name: str
    driver: str
    port: str
    datatype: str
    print_processor: str
    status: str
    location: str
    is_default: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _open(name: str) -> int:
    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(name, ctypes.byref(handle), None):
        raise PrinterError(
            f"cannot open printer {name!r}: ctypes.WinError(ctypes.get_last_error())"
        )
    return handle.value  # keep the raw handle value; HANDLE is closed by the caller


def _close(handle: int) -> None:
    winspool.ClosePrinter(wintypes.HANDLE(handle))


def list_printers() -> list[PrinterInfo]:
    """All installed printers.

    Level 1 of EnumPrinters only yields names, so the per-printer level 2 query is
    what supplies the driver, port and datatype that the print path needs.
    """
    import win32print

    default = win32print.GetDefaultPrinter()
    out: list[PrinterInfo] = []
    for level1 in win32print.EnumPrinters(
        win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    ):
        name = level1[2]
        driver = ""
        port = ""
        datatype = ""
        processor = ""
        status = ""
        location = ""
        try:
            handle = win32print.OpenPrinter(name)
            try:
                info = win32print.GetPrinter(handle, 2)
            finally:
                win32print.ClosePrinter(handle)
            driver = str(info.get("pDriverName", "") or "")
            port = str(info.get("pPortName", "") or "")
            datatype = str(info.get("pDatatype", "") or "")
            processor = str(info.get("pPrintProcessor", "") or "")
            status = str(info.get("Status", "") or "")
            location = str(info.get("pLocation", "") or "")
        except Exception:  # noqa: BLE001 - a broken queue entry must not hide the rest
            pass

        out.append(
            PrinterInfo(
                name=name,
                driver=driver,
                port=port,
                datatype=datatype,
                print_processor=processor,
                status=status,
                location=location,
                is_default=(name == default),
            )
        )
    return sorted(out, key=lambda p: p.name.lower())


@dataclass
class DevMode:
    """A DEVMODE plus the driver's private block.

    Epson keeps Media Type, Print Quality and its own colour controls inside the
    `dmDriverExtra` bytes that follow the DEVMODE. There is no public structure
    for them, so they are exposed as raw bytes for inspection only; they are not
    written by this module.
    """

    dm: DEVMODEW
    driver_extra: bytes

    def summary(self) -> dict[str, Any]:
        data = self.dm.summary()
        data["driver_extra_len"] = len(self.driver_extra)
        data["driver_extra_head"] = self.driver_extra[:64].hex(" ")
        return data


def get_devmode(printer: str) -> DevMode:
    handle = _open(printer)
    try:
        # First call reports the required size; Epson drivers use ~8 KB.
        needed = winspool.DocumentPropertiesW(
            None, wintypes.HANDLE(handle), printer, None, None, 0
        )
        if needed <= 0:
            raise PrinterError(
                f"DocumentProperties failed for {printer!r}: "
                f"ctypes.WinError(ctypes.get_last_error())"
            )
        buf = ctypes.create_string_buffer(needed)
        rc = winspool.DocumentPropertiesW(
            None, wintypes.HANDLE(handle), printer, buf, None, DM_OUT_BUFFER
        )
        if rc < 0:
            raise PrinterError(f"cannot read DEVMODE of {printer!r}: ctypes.WinError(ctypes.get_last_error())")
        raw = buf.raw[:needed]
        if raw[68:70] != ctypes.sizeof(DEVMODEW).to_bytes(2, "little"):
            # dmSize is the driver's own struct size. A pre-colour-management driver
            # reports a shorter one, and casting it into DEVMODEW would read the tail
            # fields (dmICMMethod and everything after) straight out of the private
            # block as if they were real values.
            reported = int.from_bytes(raw[68:70], "little")
            raise PrinterError(
                f"{printer!r} reports a DEVMODE of {reported} bytes, this build expects "
                f"{ctypes.sizeof(DEVMODEW)}. The colour-management fields cannot be read "
                "from it; the driver predates them."
            )
        dm = DEVMODEW.from_buffer_copy(raw)
        extra_start = ctypes.sizeof(DEVMODEW)
        extra = raw[extra_start : extra_start + dm.dmDriverExtra]
        return DevMode(dm=dm, driver_extra=extra)
    finally:
        _close(handle)


def set_devmode(printer: str, dm: DEVMODEW, driver_extra: bytes = b"") -> None:
    """Write a DEVMODE back as the printer's default settings."""
    handle = _open(printer)
    try:
        extra = driver_extra if driver_extra else bytes(dm.dmDriverExtra)
        # dmDriverExtra describes the block that follows, so it has to stay
        # consistent with what is actually sent. The field is patched *before* the
        # buffer is built: taking bytes(dm) first left the sent header describing
        # the previous length.
        dm.dmDriverExtra = len(extra)
        body = bytes(dm)
        buf = ctypes.create_string_buffer(body + extra)
        rc = winspool.DocumentPropertiesW(
            None,
            wintypes.HANDLE(handle),
            printer,
            None,
            ctypes.cast(buf, ctypes.c_void_p),
            DM_IN_BUFFER,
        )
        if rc < 0:
            raise PrinterError(
                f"cannot apply DEVMODE to {printer!r}: ctypes.WinError(ctypes.get_last_error())"
            )
    finally:
        _close(handle)


def apply_print_defaults(
    printer: str,
    *,
    copies: int | None = None,
    orientation: int | None = None,
    paper_size: int | None = None,
    resolution: int | None = None,
    media_type: int | None = None,
    color: bool | None = None,
    icm: str = "off",
    icm_intent: int | None = None,
) -> dict[str, Any]:
    """Write the printer default DEVMODE and report what the driver actually kept.

    Epson's drivers accept the write and then revert every public field to their
    stored defaults: resolution, colour-management method and intent all come back
    unchanged. Media type, quality and the driver's own colour controls live in
    the private `dmDriverExtra` block and have no public API, so the honest result
    is a per-field record of what was kept and what was ignored. Callers should
    read `ignored` rather than assume the write took effect.
    """
    current = get_devmode(printer)
    dm = current.dm

    requested: dict[str, Any] = {}

    def set_field(attr: str, flag: int, value: Any) -> None:
        requested[attr] = value
        setattr(dm, attr, value)
        dm.dmFields |= flag

    if copies is not None:
        set_field("dmCopies", DM_COPIES, max(1, int(copies)))
    if orientation is not None:
        set_field("dmOrientation", DM_ORIENTATION, int(orientation))
    if paper_size is not None:
        set_field("dmPaperSize", DM_PAPERSIZE, int(paper_size))
    if resolution is not None:
        set_field("dmPrintQuality", DM_PRINTQUALITY, int(resolution))
        set_field("dmYResolution", DM_YRESOLUTION, int(resolution))
    if media_type is not None:
        set_field("dmMediaType", DM_MEDIATYPE, int(media_type))
    if color is not None:
        set_field(
            "dmColor",
            DM_COLOR,
            DMCOLOR_COLOR if color else DMCOLOR_MONOCHROME,
        )
    if icm in ("off", "on"):
        set_field("dmICMMethod", DM_ICMMETHOD, ICM_OFF if icm == "off" else ICM_ON)
    if icm_intent is not None:
        set_field("dmICMIntent", DM_ICMINTENT, int(icm_intent))

    set_devmode(printer, dm, current.driver_extra)

    after = get_devmode(printer).dm
    kept: dict[str, Any] = {}
    ignored: list[str] = []
    for attr, value in requested.items():
        actual = getattr(after, attr)
        if actual == value:
            kept[attr] = actual
        else:
            ignored.append(f"{attr}: requested {value}, driver kept {actual}")

    result = after.summary()
    result["requested"] = requested
    result["kept"] = kept
    result["ignored"] = ignored
    return result


def list_capabilities(printer: str, timeout: float = 20.0) -> dict[str, object]:
    """The driver's own media/paper/quality lists, if it publishes them.

    DeviceCapabilities is the only public route to these and Epson's drivers fault
    inside the DLL, so the query runs in a child process. A driver that crashes
    costs the child its exit code, not the caller.
    """
    import subprocess

    result: dict[str, object] = {
        "printer": printer,
        "lists": {},
        "available": False,
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "print_mcp.capscan", printer],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
    except subprocess.TimeoutExpired:
        result["error"] = f"driver did not answer DeviceCapabilities within {timeout:.0f}s"
        return result

    if proc.returncode != 0 or not proc.stdout.strip():
        result["error"] = (
            "the driver does not implement DeviceCapabilities; it faulted or returned "
            f"nothing (exit {proc.returncode}). This is expected for Epson, which keeps "
            "its media and quality lists private."
        )
        return result

    payload = json.loads(proc.stdout)
    result["lists"] = payload.get("lists", {})
    result["available"] = any(payload.get("lists", {}).get(k) for k in
                              ("media_types", "paper_sizes", "print_qualities"))
    return result


def _device_capabilities(printer: str, feature: str, out_buffer_len: int) -> bytes:
    """Raw DeviceCapabilities call. Needs a printer HDC, hence gdi32.

    Wrapped in its own process-level guard: some drivers fault here, and an
    access violation inside a DLL would otherwise take the whole MCP server down.
    """
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.CreateDCW.argtypes = [wintypes.LPWSTR, wintypes.LPWSTR]
    gdi32.CreateDCW.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL

    hdc = gdi32.CreateDCW(printer, None)
    if not hdc:
        raise PrinterError(f"cannot get a device context for {printer!r}")
    try:
        buf = ctypes.create_string_buffer(out_buffer_len)
        rc = winspool.DeviceCapabilitiesW(hdc, feature, buf, None, out_buffer_len)
        if rc < 0:
            raise PrinterError(
                f"DeviceCapabilities({feature!r}) failed on {printer!r}: "
                f"ctypes.WinError(ctypes.get_last_error())"
            )
        # rc is the required buffer size in bytes and is negative on failure for
        # the name features; the driver writes a count as the first int.
        count = ctypes.cast(buf, ctypes.POINTER(ctypes.c_int)).contents.value
        usable = min(count * 4, out_buffer_len) if count > 0 else 0
        return buf.raw[:usable]
    finally:
        gdi32.DeleteDC(hdc)


def _capability_names(printer: str, feature: str, name_feature: str) -> list[str]:
    """Paired index/name lists from the driver (DC_PAPERS + DC_PAPERNAMES etc.)."""
    try:
        values = _device_capabilities(printer, feature, 4096)
    except PrinterError:
        return []
    import struct

    ids = list(struct.unpack(f"<{len(values) // 4}i", values[: (len(values) // 4) * 4]))
    try:
        names = _device_capabilities(printer, name_feature, 65536)
    except PrinterError:
        return [str(i) for i in ids]

    decoded = names.decode("utf-16-le", errors="replace")
    name_list = [n for n in decoded.split("\x00") if n]
    out: list[str] = []
    for idx, ident in enumerate(ids):
        label = name_list[idx] if idx < len(name_list) else f"index {ident}"
        out.append(f"{ident}: {label}")
    return out


def list_media_types(printer: str) -> list[str]:
    return _capability_names(printer, "dmMediaType", "dmMediaTypeName")


# --- spooler ----------------------------------------------------------------


def queue_status(printer: str | None = None) -> list[dict[str, Any]]:
    import win32print

    jobs: list[dict[str, Any]] = []
    printers = [printer] if printer else [p.name for p in list_printers()]
    for name in printers:
        try:
            handle = win32print.OpenPrinter(name)
        except Exception:  # noqa: BLE001 - a stale queue must not break the listing
            continue
        try:
            for job in win32print.EnumJobs(handle, 0, -1):
                jobs.append(
                    {
                        "printer": name,
                        "id": int(job["pJobID"]),
                        "document": job.get("pDocument", ""),
                        "user": job.get("pUserName", ""),
                        "status": int(job.get("Status", 0)),
                        "size_kb": int(job.get("Size", 0)),
                        "submitted": str(job.get("Submitted", "")),
                    }
                )
        finally:
            win32print.ClosePrinter(handle)
    return jobs


def cancel_job(printer: str, job_id: int) -> bool:
    import win32print

    handle = win32print.OpenPrinter(printer)
    try:
        win32print.SetJob(handle, int(job_id), 0, win32print.JOB_CONTROL_CANCEL)
        return True
    finally:
        win32print.ClosePrinter(handle)
