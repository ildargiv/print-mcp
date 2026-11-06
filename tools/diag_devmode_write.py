"""Does the Epson driver accept DEVMODE writes at all?

Applying a profile reported that the driver kept 720 dpi and ICM_DEVMATCH, so
either the write was rejected or the driver ignores the public fields and drives
printing from its private `dmDriverExtra` block. Those need telling apart, because
the answer decides whether media type and colour policy can be set from Python at
all or must be set once in the driver UI.

Each field is written and then read back, and the flags DM_IN_BUFFER and
DM_PERSIST are compared.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from print_mcp import printers

PRINTER = "EPSON Stylus Photo 1410 Series"

# Fields to try, chosen so each tests a different thing:
#  copies      - trivially supported, proves writes work at all
#  printQuality- where Epson keeps dpi
#  icmMethod   - the colour-management switch
#  mediaType   - Epson-specific media id
#  paperLength - a standard field the driver definitely models
FIELDS = {
    "copies": ("dmCopies", 3, printers.DM_COPIES),
    "printQuality": ("dmPrintQuality", 576, printers.DM_PRINTQUALITY),
    "icmMethod": ("dmICMMethod", printers.ICM_OFF, printers.DM_ICMMETHOD),
    "icmIntent": ("dmICMIntent", printers.ICM_INTENT_RELATIVE, printers.DM_ICMINTENT),
    "mediaType": ("dmMediaType", 271, printers.DM_MEDIATYPE),
    "paperLength": ("dmPaperLength", 2100, printers.DM_PAPERLENGTH),
}

MODES = {
    "DM_IN_BUFFER": printers.DM_IN_BUFFER,
    "DM_IN_BUFFER|DM_PERSIST": printers.DM_IN_BUFFER | printers.DM_PERSIST,
}


def main() -> int:
    print(f"printer: {PRINTER}\n")
    for mode_name, mode in MODES.items():
        print(f"=== {mode_name} ===")
        for label, (attr, value, flag) in FIELDS.items():
            before = printers.get_devmode(PRINTER)
            old = getattr(before.dm, attr)

            dm = before.dm
            setattr(dm, attr, value)
            dm.dmFields |= flag
            try:
                printers.set_devmode(PRINTER, dm, before.driver_extra)
                write = "write ok"
            except printers.PrinterError as exc:
                print(f"  {label:<14} WRITE FAILED: {exc}")
                continue

            after = getattr(printers.get_devmode(PRINTER).dm, attr)
            status = "HELD" if after == value else f"IGNORED (still {after})"
            print(f"  {label:<14} {write:<9} {old} -> requested {value:<4} : {status}")
        print()

    # Leave the printer as it was found.
    printers.set_devmode(PRINTER, printers.get_devmode(PRINTER).dm)
    print("driver restored to its current defaults")

    # Does the driver expose the public media/quality name lists at all?
    print("\n=== DeviceCapabilities ===")
    for feature, name_feature in (
        ("dmMediaType", "dmMediaTypeName"),
        ("dmPaperSize", "dmPaperName"),
        ("dmPrintQuality", "dmPrintQualityName"),
    ):
        try:
            values = printers._capability_names(PRINTER, feature, name_feature)
            print(f"  {feature}: {len(values)} entries -> {values[:6]}")
        except printers.PrinterError as exc:
            print(f"  {feature}: {exc}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
