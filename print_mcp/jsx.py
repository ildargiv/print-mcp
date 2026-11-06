"""ExtendScript for Photoshop printing.

What this module can and cannot do was established by probing Photoshop 26.11,
not assumed:

WORKS
    `app.open`, `doc.close`, `app.documents.add`, `app.displayDialogs`, and
    `app.executeAction(stringIDToTypeID("print"), undefined, DialogModes.NO)`,
    which prints using the settings saved in the Print dialog.

DOES NOT WORK in 26.11
    Any print ActionDescriptor. Every one is refused with "the parameters of the
    Print command are currently invalid", including an empty one, and the same
    happens over a COM string and over a .jsx file, so it is not a host quirk.
    `Document.convertProfile` is refused too. `FlipDirection` is unreachable, so
    `flipCanvas` cannot be called. `JSON`, `chr` and `require` are undefined.

Consequences for the design:

* Printer profile, rendering intent and gamut compensation come from the Print
  dialog's saved settings, configured once in the UI. They are the one part of
  the colour chain that cannot be scripted, so they are stated plainly in the MCP
  tool descriptions instead of being pretended at.
* Everything the driver owns - media type, resolution, copies and
  `dmICMMethod` - is driven from Python. `dmICMMethod = ICM_OFF` is what
  actually turns the driver's automatic colour correction off, and it is the
  part that matters most for colour fidelity.
* Mirroring, needed for sublimation, happens on a temporary copy of the file
  because Photoshop cannot flip. See `runner.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ExtendScript in this Photoshop build has no JSON object, so every script carries
# its own serializer. It escapes everything above U+007F because the COM round
# trip mangles non-ASCII and these paths are usually Cyrillic.
JSON_HELPER = r"""
    function J(v) {
        if (v === null || v === undefined) { return "null"; }
        var t = typeof v;
        if (t === "boolean") { return v ? "true" : "false"; }
        if (t === "number") { return isFinite(v) ? String(v) : "null"; }
        if (t === "string") {
            var out = '"', i, code, hex;
            for (i = 0; i < v.length; i++) {
                code = v.charCodeAt(i);
                if (code === 34) { out += '\\"'; }
                else if (code === 92) { out += "\\\\"; }
                else if (code === 10) { out += "\\n"; }
                else if (code === 13) { out += "\\r"; }
                else if (code === 9) { out += "\\t"; }
                else if (code < 32 || code > 126) {
                    hex = code.toString(16);
                    while (hex.length < 4) { hex = "0" + hex; }
                    out += "\\u" + hex;
                } else { out += v.charAt(i); }
            }
            return out + '"';
        }
        if (v instanceof Array) {
            var arr = [];
            for (var j = 0; j < v.length; j++) { arr.push(J(v[j])); }
            return "[" + arr.join(",") + "]";
        }
        var parts = [];
        for (var k in v) {
            if (v.hasOwnProperty(k)) { parts.push(J(k) + ":" + J(v[k])); }
        }
        return "{" + parts.join(",") + "}";
    }
"""


@dataclass
class PrintJob:
    """One file to print.

    `mirror` is accepted for documentation parity with the profile but is applied
    before Photoshop sees the file, so it is not sent here.
    """

    file_path: str
    printer: str
    """Recorded for the report. Photoshop actually prints to whatever printer is
    selected in its Print dialog; the name cannot be set from a script."""

    keep_open: bool = False
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {
            "file": self.file_path,
            "printer": self.printer,
            "keep_open": self.keep_open,
            "notes": self.notes,
        }


def build_print_job_js(job: PrintJob) -> str:
    """Open the file, print it with the saved dialog settings, close it.

    The print action is called with an undefined descriptor on purpose: it is the
    only form this Photoshop accepts, and it uses the settings from the Print
    dialog.
    """
    path = json.dumps(job.file_path, ensure_ascii=True)
    close = "" if job.keep_open else "        doc.close(SaveOptions.DONOTSAVECHANGES);\n"
    return f"""
#target photoshop
(function () {{
{JSON_HELPER}    var report = {{ ok: false, printed: false }};
    var doc = null;
    var savedDialogs = app.displayDialogs;
    try {{
        var f = new File({path});
        if (!f.exists) {{
            report.stage = "open";
            report.error = "file not found: " + f.fsName;
            return J(report);
        }}

        doc = app.open(f);
        report.document = {{ name: doc.name, width: String(doc.width),
                             height: String(doc.height),
                             resolution: String(doc.resolution),
                             mode: String(doc.mode) }};

        // Undefined descriptor: the only form the print action accepts here.
        app.displayDialogs = DialogModes.NO;
        app.executeAction(stringIDToTypeID("print"), undefined, DialogModes.NO);
        report.printed = true;

{close}        report.ok = true;
    }} catch (e) {{
        report.stage = report.stage || "print";
        report.error = String(e.message || e);
    }} finally {{
        app.displayDialogs = savedDialogs;
        if (doc && !KEEP_OPEN) {{
            try {{ doc.close(SaveOptions.DONOTSAVECHANGES); }} catch (ignore) {{}}
        }}
    }}
    return J(report);
}})()
""".replace("KEEP_OPEN", "true" if job.keep_open else "false")


def build_diagnostics_js() -> str:
    """Report which script-host features this Photoshop actually offers.

    Worth having as a tool: the print path depends on a small set of host
    capabilities, and a Photoshop update could change them.
    """
    features = [
        ("app.open", "app.open"),
        ("app.documents.add", "app.documents.add"),
        ("app.displayDialogs", "app.displayDialogs"),
        ("stringIDToTypeID(print)", 'stringIDToTypeID("print")'),
        ("enum: Intent", "Intent"),
        ("enum: SaveOptions", "SaveOptions"),
        ("enum: DialogModes", "DialogModes"),
        ("enum: FlipDirection", "FlipDirection"),
        ("global: JSON", "JSON"),
        ("global: require", "require"),
        ("global: chr", "chr"),
        ("global: eval", "eval"),
    ]
    checks = ",\n        ".join(
        f'{{ name: {json.dumps(name)}, expr: {json.dumps(expr)} }}' for name, expr in features
    )
    return f"""
#target photoshop
(function () {{
{JSON_HELPER}    var CHECKS = [
        {checks}
    ];
    var report = {{ capabilities: {{}}, open_documents: app.documents.length }};
    for (var i = 0; i < CHECKS.length; i++) {{
        var state = "available";
        try {{ eval(CHECKS[i].expr); }} catch (e) {{ state = "unavailable"; }}
        report.capabilities[CHECKS[i].name] = state;
    }}
    report.notes = [
        "print descriptors are refused in this build; printing uses the Print dialog settings",
        "Document.convertProfile is refused, so the destination profile is applied via the dialog",
        "FlipDirection is unavailable, so mirroring is done on a temporary file copy"
    ];
    return J(report);
}})()
"""
