"""convertProfile and flipCanvas with matched profile/document spaces.

The previous run converted a CMYK document into an RGB output profile, which
Photoshop refuses for good reason, so that throw said nothing about the host.
This tries matched pairs, and uses the numeric flip constant because
`FlipDirection` is not defined in this restricted host.
"""

from __future__ import annotations

import sys
from pathlib import Path

from print_mcp import icc
from print_mcp.photoshop import Photoshop

# (profile hint, document colour mode to create, label)
CASES = [
    ("1410 Sublim TKANI photo bumaga", "RGB", "RGB doc + RGB/output profile"),
    ("1410 840", "CMYK", "CMYK doc + CMYK/output profile"),
    ("RevL805G230Link-9-1-YC2", "RGB", "RGB doc + device-link profile"),
]

SCRIPT = r"""
#target photoshop
(function () {
    var out = [];
    function addDoc(mode) {
        return app.documents.add(
            UnitValue(16, "px"), UnitValue(16, "px"), 300, mode,
            NewDocumentMode.RGB, DocumentFill.WHITE);
    }

    var saved = app.displayDialogs;
    app.displayDialogs = DialogModes.NO;

    try {
        out.push("numeric flip test:");
        var d0 = addDoc("RGB");
        try {
            d0.flipCanvas(0);
            out.push("  flipCanvas(0) OK");
            d0.flipCanvas(0);
            out.push("  flipCanvas(0) back OK");
        } catch (e) { out.push("  flipCanvas(0) THREW: " + e.message); }
        d0.close(SaveOptions.DONOTSAVECHANGES);

        out.push("Intent members: " + (Intent.RELATIVECOLORIMETRIC) + " / " +
                 (Intent.ABSOLUTECOLORIMETRIC) + " / " + (Intent.PERCEPTUAL) + " / " +
                 (Intent.SATURATION));

        for (var i = 0; i < CASES.length; i++) {
            var c = CASES[i];
            var label = "  [" + c.label + "]";
            var doc;
            try { doc = addDoc(c.mode); }
            catch (e) { out.push(label + " addDoc THREW: " + e.message); continue; }

            try {
                out.push(label + " before: " + String(doc.mode));
                doc.convertProfile(c.path, Intent.RELATIVECOLORIMETRIC, true, false);
                out.push(label + " convertProfile OK -> after: " + String(doc.mode));
            } catch (e) {
                out.push(label + " convertProfile THREW: " + e.message);
            }
            try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (ig) {}
        }
    } finally {
        app.displayDialogs = saved;
    }
    return out.join(String.fromCharCode(10));
})()
"""


def main() -> int:
    cases: list[dict[str, str]] = []
    for hint, mode, label in CASES:
        matches = icc.find_profile(hint)
        if not matches:
            print(f"skip: no profile matching {hint!r}")
            continue
        p = matches[0]
        print(f"{label}: {p.description} [{p.color_space}/{p.device_class}]")
        cases.append({"path": str(p.path), "mode": mode, "label": label})
    if not cases:
        return 1
    print()

    app = Photoshop()
    path = Path(__file__).with_name("_convertprobe2.jsx")
    path.write_text(SCRIPT.replace("CASES", _js_array(cases)), encoding="utf-8")
    print(app.call("convertProfile probe", lambda: app.app.DoJavaScriptFile(str(path))))
    return 0


def _js_array(cases: list[dict[str, str]]) -> str:
    import json

    payload = [
        {"path": c["path"].replace("\\", "\\\\"), "mode": c["mode"], "label": c["label"]}
        for c in cases
    ]
    return json.dumps(payload, ensure_ascii=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
