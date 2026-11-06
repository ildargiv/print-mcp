import ctypes, sys
from ctypes import wintypes
sys.path.insert(0,".")
from print_mcp import printers

P = "EPSON Stylus Photo 1410 Series"
DM_IN_BUFFER=0x00000002; DM_PERSIST=0x00000004
ws = ctypes.WinDLL("winspool.drv", use_last_error=True)
ws.DocumentPropertiesW.argtypes=[wintypes.HWND, wintypes.HANDLE, wintypes.LPWSTR,
                                 ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
ws.DocumentPropertiesW.restype=ctypes.c_int
ws.OpenPrinterW.argtypes=[wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]

h = wintypes.HANDLE()
ws.OpenPrinterW(P, ctypes.byref(h), None)

def dev():
    need = ws.DocumentPropertiesW(None, h, P, None, None, 0)
    b = ctypes.create_string_buffer(need)
    ws.DocumentPropertiesW(None, h, P, b, None, 1)
    raw = b.raw[:need]
    return printers.DEVMODEW.from_buffer_copy(raw), raw[ctypes.sizeof(printers.DEVMODEW):need]

before, extra_before = dev()
print("ДО   : quality=%s icm=%s yres=%s" % (before.dmPrintQuality, before.dmICMMethod, before.dmYResolution))

# пробуем DM_IN_BUFFER | DM_PERSIST
dm = printers.DEVMODEW.from_buffer_copy(bytes(before))
dm.dmPrintQuality = 576
dm.dmYResolution = 576
dm.dmICMMethod = printers.ICM_OFF
dm.dmFields |= printers.DM_PRINTQUALITY | printers.DM_YRESOLUTION | printers.DM_ICMMETHOD
extra = extra_before
dm.dmDriverExtra = len(extra)
buf = ctypes.create_string_buffer(bytes(dm)+extra)
rc = ws.DocumentPropertiesW(None, h, P, None, ctypes.cast(buf, ctypes.c_void_p), DM_IN_BUFFER|DM_PERSIST)
print("DocumentProperties rc =", rc, "last_err =", ctypes.get_last_error())

after, _ = dev()
print("ПОСЛЕ: quality=%s icm=%s yres=%s" % (after.dmPrintQuality, after.dmICMMethod, after.dmYResolution))
ok = after.dmPrintQuality==576 and after.dmICMMethod==printers.ICM_OFF
print("РЕЗУЛЬТАТ:", "ЗАПИСАЛОСЬ" if ok else "драйвер опять откатил")
