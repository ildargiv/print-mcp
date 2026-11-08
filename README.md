# photoshop-print-mcp

MCP server that gives an AI agent **colour-accurate batch printing through Adobe Photoshop**:
print a folder of finished images with your own printer ICC profile, your paper type and quality,
and driver colour auto-correction switched **off**. 16 tools: print profiles, plan-before-print,
spooler queue, ICC search, DEVMODE inspection, sublimation mirror.

Windows · Photoshop 26.x · Python 3.10+ · MIT · [Русская версия](docs/README.ru.md)

> Repository name: `print-mcp` (to be renamed `photoshop-print-mcp`; the old URL will keep redirecting).
> The Python package is still `print_mcp`.

## Which one: this or printshop-mcp?

Both print through Windows, both keep print profiles and can mirror for sublimation, but they solve different jobs.

| | **photoshop-print-mcp** (this repo) | [**printshop-mcp**](https://github.com/ildargiv/printshop-mcp) |
|---|---|---|
| Starting point | finished image files (JPG, PNG, TIFF, PSD), printed as a folder | a design from scratch (HTML or PDF): layout, proof, print |
| Engine | Adobe Photoshop over COM | Windows print system, rendered straight to device pixels |
| Colour | fixed ICC pipeline, driver auto-correction off | profiles with the full driver state, calibration and ICC |
| Needs | Photoshop 26.x, one-time setup in two dialogs | `uv` and Edge or Chrome, no Photoshop |
| Pick it when | you already have the files and Photoshop colour management is your workflow | you want badges, cards, flyers or mixed sheets laid out and proofed by the agent |

Not sure? Start with printshop-mcp; use this server when the colour pipeline has to go through Photoshop.

## Install

Needs Windows, Photoshop 26.x and [uv](https://docs.astral.sh/uv/). Any MCP client works (Claude Desktop, Cursor, Codex, opencode...):

```json
{ "mcpServers": { "photoshop-print": { "command": "uvx",
    "args": ["--from", "git+https://github.com/ildargiv/print-mcp", "photoshop-print-mcp"] } } }
```

From a clone instead (`pip install -r requirements.txt` installs `pywin32` and `Pillow`):

```json
{ "mcpServers": { "photoshop-print": { "command": "python",
    "args": ["-m", "print_mcp.server"],
    "env": { "PYTHONPATH": "C:\\path\\to\\print-mcp" } } } }
```

Then do the [one-time setup](#one-time-setup) in Photoshop and the printer driver.

## What it does

- prints a folder or a single file: open in Photoshop → print → close without saving
- mirrors the image automatically (sublimation transfers are glued face down)
- pause between files, stop on first error, a report for every file
- print profiles: save, validate, apply
- spooler queue: list jobs, cancel
- finds and checks ICC profiles, including custom ones
- reads the printer DEVMODE and checks that the driver's colour management is off

## What it cannot do, and why

This is measured, not unfinished. Results come from probes on Photoshop 26.11; the script that produced each one is in `tools/`, and `print_mcp/jsx.py` documents the exact outcomes.

| Task | Status | How it is done |
|---|---|---|
| Print a folder of files | ✅ automatic | open in Photoshop → `print` → close |
| Mirror for sublimation | ✅ automatic | a temporary copy is mirrored |
| Pauses, stop on error, report | ✅ automatic | the server |
| Choose the printer | ⚙️ once, by hand | Photoshop print dialog |
| Paper type, quality, dpi | ⚙️ once, by hand | printer driver dialog |
| ICC profile, intent, compensation | ⚙️ once, by hand | Photoshop print dialog |
| Turn off driver colour correction | ⚙️ once, by hand | Printer properties → Colour → "No colour correction" |

Why it cannot be scripted:

1. **Photoshop 26.11 rejects every descriptor for the `print` command**, even an empty one, the same way through a COM string and a `.jsx` file. Only `executeAction("print", undefined, NO)` works, and it prints with the settings from the dialog.
2. **`Document.convertProfile` is rejected too**, so ICC conversion cannot be scripted.
3. **`FlipDirection` is unreachable** by any path tried, so `flipCanvas` cannot be called; mirroring is done on a copy of the file instead.
4. **The Epson driver accepts a DEVMODE write and ignores every public field.** Checked on an `EPSON Stylus Photo 1410`: `dmPrintQuality` 576 stayed 720, `dmICMMethod` 0 stayed 3, `dmPaperLength` did not change. Epson keeps paper type, quality and colour settings in a private `dmDriverExtra` block (796 bytes) with no public API.
5. **`DeviceCapabilities` crashes the process on Epson** (access violation inside the driver DLL), so the media list query runs in a subprocess, `print_mcp/capscan.py`.

Also, this scripting host does not define `JSON`, `require`, `chr`, `app.photoshopVersion` or `app.fullName`, hence the custom serializer in the scripts.

## One-time setup

**In Photoshop** (File → Print):

- Printer: your printer
- Colour handling: **Photoshop Manages Colors**
- Printer profile: your own ICC (for example `1410 Sublim TKANI photo bumaga`)
- Rendering intent compensation: **None**. This is what "auto-correction off" means here
- Copies: 1
- Position and size: fit to paper size

**In the printer driver** (Printing Preferences → Advanced → Colour, Epson wording):

- Colour correction: **No colour correction**
- Media type: your paper
- Quality: as needed
- Resolution: as needed (576×576 dpi for photos)

Check that the driver does not touch colour with `print_printer_info` → `icm_method`. It must be `0` (ICM_OFF). If it is `3`, the driver replaces colour after Photoshop.

## Use from an agent

Save a profile once:

```
print_save_profile {
  "name": "sublim 1410",
  "printer": "EPSON Stylus Photo 1410 Series",
  "icc": "1410 Sublim TKANI photo bumaga",
  "mirror": true,
  "delay_seconds": 2,
  "stop_on_error": true
}
```

Check that everything fits together:

```
print_validate_profile {"name": "sublim 1410"}
```

See the plan without printing:

```
print_plan {"name": "sublim 1410", "folder": "D:\\print-samples"}
```

Print:

```
print_folder {"name": "sublim 1410", "folder": "D:\\print-samples"}
```

## Tools

| Tool | Purpose |
|---|---|
| `print_capabilities` | what the scripting host of this Photoshop version can do |
| `print_status` | version, busy state, open documents |
| `print_open_documents` | open documents with size and mode |
| `print_list_printers` | installed printers |
| `print_printer_info` | DEVMODE and what the driver publishes about itself |
| `print_find_icc` | search installed ICC profiles |
| `print_list_profiles` | saved print profiles |
| `print_save_profile` | create or overwrite a profile |
| `print_delete_profile` | delete a profile |
| `print_validate_profile` | check a profile before a run |
| `print_apply_driver` | try a DEVMODE write and report what the driver ignored |
| `print_plan` | what a folder would print, without printing |
| `print_file` | print one file |
| `print_folder` | print a folder |
| `print_queue` | spooler queue |
| `print_cancel` | cancel a job |

## Run without an agent

```powershell
cd C:\path\to\print-mcp
$env:PYTHONPATH = "C:\path\to\print-mcp"
python tools\mcp_call.py --list
python tools\mcp_call.py print_plan "@tools\args_plan.json"
```

`mcp_call.py` takes arguments as `@file.json`, because PowerShell strips quotes from inline JSON.

## Format limits

- **Mirror** works for JPG, PNG and TIFF. PSD/PSB cannot be mirrored (Photoshop cannot flip a layered file as a flat one); such files are skipped with a clear error, never printed backwards.
- **Extensions:** `.psd .psb .tif .tiff .png .jpg .jpeg`
- **Originals are never modified:** a temporary copy is mirrored and the document is closed without saving.
- File dpi barely changes the print size when the Photoshop dialog says "fit to paper size": the paper sets the size. The mirrored copy still carries dpi and ICC over, and `print_file` warns if Photoshop opened a copy at a different resolution.

## Diagnostic scripts

`tools/` holds the scripts the conclusions above came from. They are run by hand and are not part of the server. Edit the sample paths at the top of each script first.

| File | What it checks |
|---|---|
| `diag_capabilities.py` | what the host has: open / print / enum / flip |
| `diag_convert2.py` | `convertProfile` with matching profile pairs |
| `diag_flip_enum.py` | looks for `FlipDirection` by every available path |
| `diag_devmode_write.py` | whether Epson accepts a DEVMODE write |
| `diag_dpi.py` | where Photoshop takes an open file's resolution from |
| `check_mirror_preserves.py` | the mirrored copy keeps size, dpi and ICC |
| `mcp_call.py` | call a tool without an agent; `--list` shows all |

`tools/scratch/` has older one-off probes kept for reference.

## Tests

```
python tests\test_print_mcp.py
```

14 tests; Photoshop and a printer are not needed. They cover the DEVMODEW layout, mirrored-copy geometry (size, flip, dpi, ICC, original untouched), the file filter, dpi reading and profile saving. `pytest` works but is not required.

## Related

- [printshop-mcp](https://github.com/ildargiv/printshop-mcp): design, layout, proof and print for badges, cards and flyers on any printer.
- [KopiTehnik Print](https://github.com/ildargiv/KopiTehnik-Print): inkjet photo printing without Photoshop.
