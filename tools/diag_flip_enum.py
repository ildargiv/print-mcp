"""Locate the FlipDirection enum, or confirm it is unreachable.

`doc.flipCanvas` works but insists on an enumeration value, and the global
`FlipDirection` is not defined in this host even though most other enums are
(`Intent`, `SaveOptions`, `DialogModes` and so on all resolve). Sublimation
needs a horizontal mirror, so the enum has to be found by some route, or the
mirror has to move to the file layer.

Only the lookup paths are tested here; the file is closed without printing.
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

    var lookups = [
        'FlipDirection',
        'app.FlipDirection',
        'Document.FlipDirection',
        'Photoshop.FlipDirection',
        'window.FlipDirection',
        'this.FlipDirection',
        '$.globals.FlipDirection',
        '$.global.FlipDirection',
        '$.w.FlipDirection',
        'app.document.FlipDirection',
        'new ActionDescriptor().constructor'
    ];
    for (var i = 0; i < lookups.length; i++) {
        var t = "MISSING";
        try { var v = eval(lookups[i]); t = String(v).substring(0, 70); }
        catch (e) { t = "throws: " + e.message.substring(0, 60); }
        say("  " + lookups[i] + " => " + t);
    }

    // Compare against an enum that does resolve, to learn its shape.
    try { say("Intent shape: " + String(Intent).substring(0, 70)); } catch (e) { say("Intent throws"); }
    try {
        say("Intent key: " + Intent.RELATIVECOLORIMETRIC + " typeof " +
            typeof Intent.RELATIVECOLORIMETRIC);
    } catch (e) { say("Intent member throws"); }
    try { say("SaveOptions.DONOTSAVECHANGES = " + SaveOptions.DONOTSAVECHANGES); }
    catch (e) { say("SaveOptions member throws"); }

    // Does flipCanvas accept an enum borrowed from another namespace? Probably
    // not, but confirm rather than assume.
    var f = new File(SOURCE_FILE);
    var doc = null;
    var saved = app.displayDialogs;
    app.displayDialogs = DialogModes.NO;
    try {
        doc = app.open(f);
        say("opened: " + doc.name + " " + String(doc.mode));
        try { doc.flipCanvas(SaveOptions.DONOTSAVECHANGES);
              say("flipCanvas(SaveOptions.DONOTSAVECHANGES) => OK (type not checked?)"); }
        catch (e) { say("flipCanvas(SaveOptions...) => THREW: " + e.message); }
    } catch (e) {
        say("open failed: " + e.message);
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
    app = Photoshop()
    path = Path(__file__).with_name("_enum_probe.jsx")
    path.write_text(
        SCRIPT.replace("SOURCE_FILE", '"' + str(files[0]).replace("\\", "\\\\") + '"'),
        encoding="utf-8",
    )
    print(app.call("enum probe", lambda: app.app.DoJavaScriptFile(str(path))))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
