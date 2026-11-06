import sys; sys.path.insert(0,".")
from pathlib import Path
from print_mcp.runner import collect_files
for base in [r"Z:\1ПЕЧАТЬ В ПОДВАЛЕ", r"C:\Users\Pod\Desktop\PRINT"]:
    p=Path(base)
    if not p.is_dir(): print("нет папки:", base); continue
    fs = collect_files(base)[:10]
    print("==", base, "->", len(fs), "файлов")
    from PIL import Image, ImageOps
    for f in fs:
        try:
            with Image.open(f) as im:
                r = ImageOps.exif_transpose(im)
                dpi = im.info.get("dpi") or (im.info.get("jfif_density") if im.info.get("jfif_unit")==1 else None)
                icc = bool(im.info.get("icc_profile"))
                cm = f"{r.size[0]/dpi[0]*2.54:.0f}x{r.size[1]/dpi[1]*2.54:.0f}см" if dpi else "НЕТ DPI"
                print(f"  {f.name[:34]:36} {r.size[0]}x{r.size[1]} dpi={dpi} {cm} icc={icc}")
        except Exception as e: print("  ", f.name, "ERR", e)
