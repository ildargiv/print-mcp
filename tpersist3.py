import ctypes, sys, time
from ctypes import wintypes
sys.path.insert(0,".")
from print_mcp import printers

def say(*a, **k):
    print(*a, flush=True, **k)

P = "EPSON Stylus Photo 1410 Series"
ws = ctypes.WinDLL("winspool.drv", use_last_error=True)
ws.DocumentPropertiesW.argtypes=[wintypes.HWND, wintypes.HANDLE, wintypes.LPWSTR,
                                 ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
ws.DocumentPropertiesW.restype=ctypes.c_int
ws.OpenPrinterW.argtypes=[wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
h = wintypes.HANDLE()
ws.OpenPrinterW(P, ctypes.byref(h), None)

need = ws.DocumentPropertiesW(None, h, P, None, None, 0)
b = ctypes.create_string_buffer(need)
ws.DocumentPropertiesW(None, h, P, b, None, 1)
raw=b.raw[:need]
SZ = ctypes.sizeof(printers.DEVMODEW)
dm = printers.DEVMODEW.from_buffer_copy(raw)
say("ДО: quality=%s icm=%s yres=%s" % (dm.dmPrintQuality, dm.dmICMMethod, dm.dmYResolution))
extra = raw[SZ:]
say("extra len=%d" % len(extra))

for name, mode in [("DM_IN_BUFFER только", 0x02), ("DM_IN_BUFFER|DM_PERSIST", 0x06)]:
    d = printers.DEVMODEW.from_buffer_copy(raw)
    d.dmPrintQuality=576; d.dmYResolution=576; d.dmICMMethod=printers.ICM_OFF
    d.dmFields |= printers.DM_PRINTQUALITY|printers.DM_YRESOLUTION|printers.DM_ICMMETHOD
    d.dmDriverExtra=len(extra)
    buf = ctypes.create_string_buffer(bytes(d)+extra)
    t=time.time()
    rc = ws.DocumentPropertiesW(None, h, P, None, ctypes.cast(buf, ctypes.c_void_p), mode)
    say("  %-26s %.2fs rc=%s err=%s" % (name, time.time()-t, rc, ctypes.get_last_error()), flush=True)
    b2 = ctypes.create_string_buffer(need)
    ws.DocumentPropertiesW(None, h, P, b2, None, 1)
    d2 = printers.DEVMODEW.from_buffer_copy(b2.raw[:need])
    say("  -> quality=%s icm=%s yres=%s" % (d2.dmPrintQuality, d2.dmICMMethod, d2.dmYResolution), flush=True)
