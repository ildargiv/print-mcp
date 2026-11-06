"""Query a printer driver's DeviceCapabilities lists in a throwaway process.

`DeviceCapabilities` is the only public route to a driver's media, paper and
quality name lists, and some drivers - Epson's included - fault inside the DLL
rather than returning an error. An access violation cannot be caught in-process, so
this runs as a child: a driver that crashes takes down the child and the caller
sees a non-zero exit code instead of losing the MCP server.

Usage:
    python -m print_mcp.capscan "EPSON Stylus Photo 1410 Series"
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    printer = sys.argv[1]

    from . import printers

    report: dict[str, object] = {"printer": printer, "lists": {}}
    for label, (feature, name_feature) in (
        ("media_types", ("dmMediaType", "dmMediaTypeName")),
        ("paper_sizes", ("dmPaperSize", "dmPaperName")),
        ("print_qualities", ("dmPrintQuality", "dmPrintQualityName")),
    ):
        try:
            report["lists"][label] = printers._capability_names(  # noqa: SLF001
                printer, feature, name_feature
            )
        except Exception as exc:  # noqa: BLE001
            report["lists"][label] = []
            report["lists"][f"{label}_error"] = f"{type(exc).__name__}: {exc}"

    sys.stdout.write(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
