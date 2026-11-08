"""Establish exactly which operations work in this Photoshop's script host.

Photoshop 26.11 exposes a restricted host: `JSON`, `chr`, `require` and
`FlipDirection` are all undefined, `stringIDToTypeID` resolves only registered
ids, every `executeAction` descriptor is rejected, and the `convertProfile` DOM
method is refused. What does work is `app.documents.add`,
`executeAction("print", undefined, NO)`, and plain functions.

Since the print path is settled, the remaining unknown is the mirror step that
sublimation needs, plus whether a real file can be opened and closed. This tests
only what the implementation depends on, and prints nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from print_mcp.photoshop import Photoshop

SOURCE = Path(r"D:\print-samples")

SCRIPT = r"""
#target photoshop
(function () {
    var out = [];
    function say(s) { out.push(s); }

    say("app.photoshopVersion: " + String(app.photoshopVersion));
    say("app.fullName: " + String(app.fullName));

    var enumNames = ["Intent", "NewDocumentMode", "DocumentFill", "SaveOptions",
                     "DialogModes", "FlipDirection", "BlendMode", "Units", "Type",
                     "Extension", "DocumentMode"];
    for (var i = 0; i < enumNames.length; i++) {
        var t = "undefined";
        try { t = String(eval(enumNames[i])).substring(0, 60); } catch (e) { t = "throws"; }
        say("  enum " + enumNames[i] + " = " + t);
    }

    var saved = app.displayDialogs;
    app.displayDialogs = DialogModes.NO;

    var doc = null;
    try {
        var f = new File(SOURCE_FILE);
        say("file exists: " + f.exists + "  " + f.fsName);

        try {
            doc = app.open(f);
            say("app.open OK: name=" + doc.name + " mode=" + String(doc.mode) +
                " w=" + doc.width + " h=" + doc.height + " res=" + doc.resolution);
        } catch (e) {
            say("app.open THREW: " + e.message);
        }

        if (doc) {
            // Mirror variants, in the order they are most likely to work.
            try { doc.flipCanvas(FlipDirection.HORIZONTAL);
                  say("MIRROR doc.flipCanvas(FlipDirection.HORIZONTAL) => OK"); }
            catch (e) { say("MIRROR flipCanvas(FlipDirection.HORIZONTAL) => THREW: " + e.message); }

            try { doc.flipCanvas(0); say("MIRROR doc.flipCanvas(0) => OK"); }
            catch (e) { say("MIRROR flipCanvas(0) => THREW: " + e.message); }

            try { doc.flip(0); say("MIRROR doc.flip(0) => OK"); }
            catch (e) { say("MIRROR flip(0) => THREW: " + e.message); }

            try { doc.flip(1); say("MIRROR doc.flip(1) => OK"); }
            catch (e) { say("MIRROR flip(1) => THREW: " + e.message); }

            try {
                var d = new ActionDescriptor();
                d.putBoolean(stringIDToTypeID("horizontal"), true);
                app.executeAction(stringIDToTypeID("flip"), d, DialogModes.NO);
                say("MIRROR executeAction(flip, horizontal) => OK");
            } catch (e) { say("MIRROR executeAction(flip) => THREW: " + e.message); }
        }

        try { say("stringIDToTypeID(print) = " + stringIDToTypeID("print")); }
        catch (e) { say("sid(print) THREW: " + e.message); }
    } finally {
        app.displayDialogs = saved;
        if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (ig) {} }
    }
    return out.join(String.fromCharCode(10));
})()
"""


def main() -> int:
    files = sorted(p for p in SOURCE.glob("*.jpg")) if SOURCE.is_dir() else []
    if not files:
        print(f"no jpg found under {SOURCE}")
        return 1
    target = files[0]
    print(f"testing with: {target}\n")

    app = Photoshop()
    path = Path(__file__).with_name("_capabilities.jsx")
    path.write_text(
        SCRIPT.replace("SOURCE_FILE", '"' + str(target).replace("\\", "\\\\") + '"'),
        encoding="utf-8",
    )
    print(app.call("capability probe", lambda: app.app.DoJavaScriptFile(str(path))))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
