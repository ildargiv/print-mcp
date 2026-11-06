"""Print MCP server: stdio JSON-RPC 2.0.

Hand-rolled rather than built on the `mcp` package so the server has no install
step beyond pywin32 and Pillow. The protocol surface matches the existing
EpsonPrintManager server: initialize, tools/list, tools/call, resources/list,
resources/read, ping.

The colour chain is split, and the tool descriptions say where each half lives
because that is the main thing to get right when using this:

* printer profile, rendering intent and gamut compensation are saved in
  Photoshop's Print dialog. Photoshop 26.11 refuses every print descriptor and
  its `convertProfile` method, so these cannot be set from here - configure the
  dialog once.
* media type, resolution, copies and the driver's own colour correction are set
  through the DEVMODE. `print_apply_driver` reports whether the driver accepted
  ICM_OFF, which is what makes the dialog's profile authoritative.
* mirroring, which sublimation needs, is applied to a temporary copy of each
  file, because Photoshop cannot flip.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

from . import icc, printers, runner
from .jsx import build_diagnostics_js
from .photoshop import Photoshop, PhotoshopBusy
from .runner import PrintError, PrintProfile, PrintRunner

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "print-mcp"
SERVER_VERSION = "1.0.0"

ToolFn = Callable[[dict[str, Any]], Any]
TOOLS: dict[str, ToolFn] = {}

#: See `runner.blocking`: validate() notes that describe the split colour chain
#: rather than describe a problem.
_NON_BLOCKING = runner.NON_BLOCKING
blocking = runner.blocking


def tool(name: str, description: str, schema: dict[str, Any]) -> Callable[[ToolFn], ToolFn]:
    """Register an MCP tool with its description and input schema."""

    def decorate(fn: ToolFn) -> ToolFn:
        TOOLS[name] = fn
        fn.__tool_meta__ = {  # type: ignore[attr-defined]
            "name": name,
            "description": description,
            "inputSchema": schema,
        }
        return fn

    return decorate


def schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


def s_str(desc: str, **kw: Any) -> dict[str, Any]:
    return {"type": "string", "description": desc, **kw}


def s_int(desc: str, **kw: Any) -> dict[str, Any]:
    return {"type": "integer", "description": desc, **kw}


def s_bool(desc: str, default: bool | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "boolean", "description": desc}
    if default is not None:
        out["default"] = default
    return out


def s_list(desc: str, item_type: str = "string") -> dict[str, Any]:
    return {"type": "array", "description": desc, "items": {"type": item_type}}


_runner: PrintRunner | None = None


def get_runner() -> PrintRunner:
    global _runner
    if _runner is None:
        _runner = PrintRunner(Photoshop())
    return _runner


# --- Photoshop / host inspection --------------------------------------------


@tool(
    "print_capabilities",
    "Which script-host features this Photoshop build offers. Worth checking after a "
    "Photoshop update: the print path depends on a narrow set of them, and this build "
    "refuses print descriptors, convertProfile and FlipDirection.",
    schema({}),
)
def print_capabilities(_args: dict[str, Any]) -> Any:
    return Photoshop().run_js_json(build_diagnostics_js())


@tool(
    "print_status",
    "Photoshop version, whether it is busy, and the documents it currently has open. "
    "Busy means Photoshop is not accepting automation, usually a modal dialog or a "
    "job in progress.",
    schema({}),
)
def print_status(_args: dict[str, Any]) -> Any:
    ps = Photoshop()
    try:
        version = ps.version()
        docs = [d.as_dict() for d in ps.documents()]
        return {"photoshop_version": version, "busy": False, "documents": docs}
    except PhotoshopBusy as exc:
        return {
            "photoshop_version": None,
            "busy": True,
            "documents": [],
            "note": "Photoshop is not accepting automation calls, most likely because a "
            "modal dialog is open or a job is in progress",
            "error": str(exc),
        }


@tool(
    "print_open_documents",
    "Documents currently open in Photoshop, with size, resolution and colour mode.",
    schema({}),
)
def print_open_documents(_args: dict[str, Any]) -> Any:
    return [d.as_dict() for d in Photoshop().documents()]


@tool(
    "print_list_printers",
    "Installed printers with driver, port, datatype and which is the Windows default.",
    schema({}),
)
def print_list_printers(_args: dict[str, Any]) -> Any:
    return [p.as_dict() for p in printers.list_printers()]


@tool(
    "print_printer_info",
    "Full DEVMODE of one printer, plus what the driver publishes about itself. "
    "icm_method 0 means the driver's colour management is off; 3 means the driver is "
    "doing device-constrained matching and will alter the colour after Photoshop. "
    "Epson drivers publish no media or quality lists and ignore DEVMODE writes, so the "
    "media type, resolution and colour controls have to be set in the driver's own UI.",
    schema({"printer": s_str("Exact printer name as Windows reports it")}, ["printer"]),
)
def print_printer_info(args: dict[str, Any]) -> Any:
    info = printers.get_devmode(args["printer"])
    summary = info.summary()
    summary["driver_extra_note"] = (
        f"{len(info.driver_extra)} opaque bytes. Epson keeps Media Type, Print Quality and "
        "its own colour controls here, with no public structure and no API, so they can "
        "only be set in the driver UI."
    )
    summary["driver_capabilities"] = printers.list_capabilities(args["printer"])
    return summary


@tool(
    "print_find_icc",
    "Installed ICC profiles, optionally filtered. device_class 'output' or 'link' is what "
    "a print job wants; 'link' (device link) is the usual choice for driving an Epson with "
    "its own ink. Note Photoshop reads the destination profile from its Print dialog, not "
    "from here - this tool exists so a profile can be verified before a batch runs.",
    schema(
        {
            "query": s_str("Case-insensitive substring of the profile description"),
            "device_class": s_str("Filter: input, display, output, link, abstract"),
            "color_space": s_str("Filter: RGB, CMYK, Gray, Lab, ..."),
            "extra_dirs": s_list("Extra folders to search for custom profiles"),
        }
    ),
)
def print_find_icc(args: dict[str, Any]) -> Any:
    profiles = icc.list_profiles(args.get("extra_dirs") or None)
    query = (args.get("query") or "").lower()
    device_class = (args.get("device_class") or "").lower()
    color_space = (args.get("color_space") or "").lower()
    out = [
        p
        for p in profiles
        if (not query or query in p.description.lower() or query in p.path.stem.lower())
        and (not device_class or p.device_class == device_class)
        and (not color_space or p.color_space.lower() == color_space)
    ]
    return {"count": len(out), "profiles": [p.as_dict() for p in out]}


# --- print profiles ---------------------------------------------------------

PROFILE_SCHEMA = schema(
    {
        "name": s_str("Profile name"),
        "printer": s_str(
            "Windows printer name. Must be the printer selected in Photoshop's Print "
            "dialog, because the script cannot choose the printer."
        ),
        "icc": s_str(
            "Destination ICC path or description substring. Verified to be installed so a "
            "typo is caught before a batch; Photoshop reads the profile from its own Print "
            "dialog, which cannot be set from a script."
        ),
        "icc_profile_dirs": s_list("Extra folders to search for custom ICC profiles"),
        "resolution": s_int(
            "Print resolution in dpi. Written to dmPrintQuality, which Epson drivers ignore; "
            "set the resolution in the driver UI."
        ),
        "media_type": s_int(
            "dmMediaType index. Epson drivers ignore this too; set the media type in the "
            "driver UI."
        ),
        "paper_size": s_int("dmPaperSize index; see print_printer_info.paper_sizes"),
        "orientation": s_int("1 = portrait, 2 = landscape"),
        "copies": s_int("Copies per file"),
        "color": s_bool("False prints in monochrome", True),
        "mirror": s_bool(
            "True for sublimation, where the transfer goes on face down, so the image must "
            "print mirrored. False for DTF and photo paper. Applied to a temporary copy; "
            "PSD/PSB cannot be mirrored automatically.",
            True,
        ),
        "delay_seconds": s_int("Pause between files, so a slow queue is not overrun"),
        "stop_on_error": s_bool("Abort the batch at the first failure"),
        "recursive": s_bool("Include subfolders when printing a folder"),
    },
    ["name", "printer"],
)


@tool("print_list_profiles", "Saved print profiles.", schema({}))
def print_list_profiles(_args: dict[str, Any]) -> Any:
    return {"profiles": [p.as_dict() for p in runner.list_profiles()]}


@tool(
    "print_save_profile",
    "Create or overwrite a print profile. Setting icc also turns the driver's colour "
    "management off when the profile is applied, so the ICC is the only thing shaping "
    "the colour.",
    PROFILE_SCHEMA,
)
def print_save_profile(args: dict[str, Any]) -> Any:
    known = set(PrintProfile.__dataclass_fields__)
    unknown = set(args) - known
    if unknown:
        raise PrintError(f"unknown profile fields: {', '.join(sorted(unknown))}")
    profile = PrintProfile(**{k: v for k, v in args.items() if k in known})
    path = runner.save_profile(profile)
    return {"saved": str(path), "profile": profile.as_dict()}


@tool(
    "print_delete_profile",
    "Delete a saved print profile.",
    schema({"name": s_str("Profile name")}, ["name"]),
)
def print_delete_profile(args: dict[str, Any]) -> Any:
    return {"deleted": runner.delete_profile(args["name"]), "name": args["name"]}


@tool(
    "print_validate_profile",
    "Check a profile before using it: printer installed, ICC present, and the driver "
    "settings that will be applied. Returns notes for the things that must be set in "
    "Photoshop's Print dialog.",
    schema({"name": s_str("Profile name")}, ["name"]),
)
def print_validate_profile(args: dict[str, Any]) -> Any:
    profile = runner.load_profile(args["name"])
    problems = profile.validate()
    stop = blocking(problems)
    out: dict[str, Any] = {"profile": profile.as_dict(), "ok": not stop, "notes": problems}
    try:
        resolved = profile.resolved_icc()
        out["icc_resolved"] = resolved.as_dict() if resolved else None
    except PrintError as exc:
        out["icc_resolved"] = None
        stop.append(str(exc))
    if not stop:
        try:
            out["driver_preview"] = printers.get_devmode(profile.printer).summary()
        except printers.PrinterError as exc:
            out["driver_preview"] = None
            stop.append(str(exc))
    out["ok"] = not stop
    out["blocking"] = stop
    return out


@tool(
    "print_apply_driver",
    "Attempt to write the printer default DEVMODE from a profile - media type, "
    "resolution, copies, ICM off - and report which fields the driver kept. Epson "
    "drivers keep none of them, so read 'ignored' and set those in the driver's own UI. "
    "Diagnostic; the print tools call this themselves.",
    schema({"name": s_str("Profile name")}, ["name"]),
)
def print_apply_driver(args: dict[str, Any]) -> Any:
    profile = runner.load_profile(args["name"])
    return get_runner().apply_driver(profile)


# --- printing ---------------------------------------------------------------


@tool(
    "print_file",
    "Print one file with a saved profile. The file is opened in Photoshop, printed with "
    "the Print dialog's saved settings, and closed without saving.",
    schema(
        {
            "name": s_str("Profile name"),
            "file": s_str("Absolute path to a PSD/PSB/TIF/PNG/JPG"),
            "apply_driver": s_bool("Write the printer DEVMODE first", True),
        },
        ["name", "file"],
    ),
)
def print_file(args: dict[str, Any]) -> Any:
    profile = runner.load_profile(args["name"])
    stop = blocking(profile.validate())
    if stop:
        return {"ok": False, "blocking": stop}
    driver = get_runner().apply_driver(profile) if args.get("apply_driver", True) else None
    result = get_runner().print_file(profile, args["file"])
    return {"driver": driver, **result.as_dict()}


@tool(
    "print_folder",
    "Print every printable file in a folder with a saved profile, in name order, with an "
    "optional pause between files and an option to stop at the first failure.",
    schema(
        {
            "name": s_str("Profile name"),
            "folder": s_str("Folder to print"),
            "recursive": s_bool("Include subfolders; overrides the profile setting"),
            "apply_driver": s_bool("Write the printer DEVMODE first", True),
            "limit": s_int("Print at most this many files"),
        },
        ["name", "folder"],
    ),
)
def print_folder(args: dict[str, Any]) -> Any:
    profile = runner.load_profile(args["name"])
    # A folder run used to sail past a profile that print_file would have refused,
    # so an uninstalled printer meant working through every file in the folder.
    stop = blocking(profile.validate())
    if stop:
        return {"ok": False, "blocking": stop}
    recursive = args.get("recursive", profile.recursive)
    files = runner.collect_files(args["folder"], recursive=recursive)
    if args.get("limit"):
        files = files[: int(args["limit"])]
    if not files:
        return {
            "ok": False,
            "error": f"no printable files in {args['folder']}",
            "supported": sorted(runner.PRINTABLE_EXTENSIONS),
        }
    unmirrorable = [
        str(f) for f in files
        if profile.mirror and f.suffix.lower() not in runner.MIRRORABLE_EXTENSIONS
    ]
    return get_runner().print_batch(
        profile, [str(f) for f in files],
        apply_driver=args.get("apply_driver", True),
        unmirrorable=unmirrorable,
    )


@tool(
    "print_plan",
    "Show what a folder run would do - the files in order, the driver settings that "
    "would be applied, and any file that cannot be printed as configured. Prints nothing.",
    schema(
        {
            "name": s_str("Profile name"),
            "folder": s_str("Folder to inspect"),
            "recursive": s_bool("Include subfolders; overrides the profile setting"),
        },
        ["name", "folder"],
    ),
)
def print_plan(args: dict[str, Any]) -> Any:
    profile = runner.load_profile(args["name"])
    recursive = args.get("recursive", profile.recursive)
    files = runner.collect_files(args["folder"], recursive=recursive)
    # validate() walks every installed printer and re-reads the ICC directories, so
    # it is called once here rather than three times.
    problems = profile.validate()
    unmirrorable = (
        [str(f) for f in files if profile.mirror and f.suffix.lower() not in runner.MIRRORABLE_EXTENSIONS]
        if profile.mirror
        else []
    )
    stop = blocking(problems)
    preview = None
    if not stop:
        try:
            preview = printers.get_devmode(profile.printer).summary()
        except printers.PrinterError as exc:
            stop.append(str(exc))
    return {
        "profile": profile.as_dict(),
        "count": len(files),
        "files": [str(f) for f in files],
        "unmirrorable": unmirrorable,
        "ok": not stop,
        "notes": problems,
        "blocking": stop,
        "driver_preview": preview,
    }


# --- spooler ----------------------------------------------------------------


@tool(
    "print_queue",
    "Jobs in the Windows print queue, optionally for one printer.",
    schema({"printer": s_str("Printer name; omit for all")}),
)
def print_queue(args: dict[str, Any]) -> Any:
    return {"jobs": printers.queue_status(args.get("printer"))}


@tool(
    "print_cancel",
    "Cancel one queued print job.",
    schema(
        {"printer": s_str("Printer name"), "job_id": s_int("Job id from print_queue")},
        ["printer", "job_id"],
    ),
)
def print_cancel(args: dict[str, Any]) -> Any:
    return {
        "cancelled": printers.cancel_job(args["printer"], int(args["job_id"])),
        "printer": args["printer"],
        "job_id": args["job_id"],
    }


# --- protocol ---------------------------------------------------------------

RESOURCES = [
    {"uri": "print://printers", "name": "Printers", "mimeType": "application/json"},
    {"uri": "print://profiles", "name": "Print profiles", "mimeType": "application/json"},
    {"uri": "print://icc", "name": "ICC profiles", "mimeType": "application/json"},
]


def _tool_list() -> list[dict[str, Any]]:
    out = [getattr(fn, "__tool_meta__") for fn in TOOLS.values() if hasattr(fn, "__tool_meta__")]
    return sorted(out, key=lambda t: t["name"])


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    fn = TOOLS.get(name)
    if fn is None:
        return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}], "isError": True}
    try:
        result = fn(arguments or {})
    except (PrintError, printers.PrinterError, icc.IccError, PhotoshopBusy, FileNotFoundError) as exc:
        return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
    except Exception as exc:  # noqa: BLE001 - the protocol has no other channel
        return {
            "content": [
                {"type": "text", "text": f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"}
            ],
            "isError": True,
        }

    text = (
        result
        if isinstance(result, str)
        else json.dumps(result, indent=2, ensure_ascii=False, default=str)
    )
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": result if isinstance(result, dict) else {"result": result},
        "isError": False,
    }


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC request. Returns None for notifications."""
    method = request.get("method")
    req_id = request.get("id")

    if method == "initialize":
        result: Any = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    elif method == "tools/list":
        result = {"tools": _tool_list()}
    elif method == "tools/call":
        params = request.get("params") or {}
        result = _call_tool(params.get("name", ""), params.get("arguments") or {})
    elif method == "resources/list":
        result = {"resources": RESOURCES}
    elif method == "resources/read":
        uri = (request.get("params") or {}).get("uri", "")
        payloads = {
            "print://printers": lambda: [p.as_dict() for p in printers.list_printers()],
            "print://profiles": lambda: [p.as_dict() for p in runner.list_profiles()],
            "print://icc": lambda: [p.as_dict() for p in icc.list_profiles()],
        }
        body = (
            json.dumps(payloads[uri](), indent=2, ensure_ascii=False, default=str)
            if uri in payloads
            else "{}"
        )
        result = {"contents": [{"uri": uri, "mimeType": "application/json", "text": body}]}
    elif method == "ping":
        result = {}
    elif method and method.startswith("notifications/"):
        return None
    else:
        if req_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    if req_id is None:
        return None
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def main() -> int:
    # stdio is the transport, so stdout carries protocol frames only.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            continue
        try:
            response = handle(request)
        except Exception as exc:  # noqa: BLE001
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
