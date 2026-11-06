import ctypes, sys, time
from ctypes import wintypes
sys.path.insert(0,".")
from print_mcp import printers

def say(*a):
    print(*a, flush=True)

P = "EPSON Stylus Photo 1410 Series"
ws = ctypes.WinDLL("winspool.drv", use_last_error=True)
ws.DocumentPropertiesW.argtypes=[wintypes.HWND, wintypes.HANDLE, wintypes.LPWSTR,
                                 ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
ws.DocumentPropertiesW.restype=ctypes.c_int
ws.OpenPrinterW.argtypes=[wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
h = wintypes.HANDLE()
t=time.time(); ws.OpenPrinterW(P, ctypes.byref(h), None)
say("open: %.2fs err=%s" % (time.time()-t, ctypes.get_last_error()))

t=time.time()
need = ws.DocumentPropertiesW(None, h, P, None, None, 0)
say("size query: %.2fs need=%s err=%s" % (time.time()-t, need, ctypes.get_last_error()))

t=time.time()
b = ctypes.create_string_buffer(need)
rc = ws.DocumentPropertiesW(None, h, P, b, None, 1)
say("read: %.2fs rc=%s" % (time.time()-t, rc))
raw=b.raw[:need]
dm = printers.DEVMODEW.from_buffer_copy(raw)
say("ДО: quality=%s icm=%s yres=%s" % (dm.dmPrintQuality, dm.dmICMMethod, dm.dmYResolution))

dm.dmPrintQuality=576; dm.dmYResolution=576; dm.dmICMMethod=printers.ICM_OFF
dm.dmFields |= printers.DM_PRINTQUALITY|printers.DM_YRESOLUTION|printers.DM_ICMMETHOD
extra = raw[ctypes.sizeof(printers.DEVMODEW):]
dm.dmDriverExtra=len(extra)
buf = ctypes.create_string_buffer(bytes(dm)+extra)
say("пробую запись DM_IN_BUFFER|DM_PERSIST ...", flush=True)
t=time.time()
rc = ws.DocumentPropertiesW(None, h, P, None, ctypes.cast(buf, ctypes.c_void_p), 0x02|0x04)
say("запись: %.2fs rc=%s err=%s" % (time.time()-t, rc, ctypes.get_last_error()), flush=True)

b2 = ctypes.create_string_buffer(need)
ws.DocumentPropertiesW(None, h, P, b2, None, 1)
d2 = printers.DEVMODEW.from_buffer_copy(b2.raw[:need])
say("ПОСЛЕ: quality=%s icm=%s yres=%s" % (d2.dmPrintQuality, d2.dmICMMethod, d2.dmYResolution))
say("ИТОГ:", "ЗАПИСАЛОСЬ" if (d2.dmPrintQuality==576 and d2.dmICMMethod==printers.ICM_OFF) else "откатил")
