import sys; sys.path.insert(0,".")
from print_mcp import printers, icc
P="EPSON Stylus Photo 1410 Series"
d = printers.get_devmode(P)
s = d.dm.summary()
ICM={0:"ICM_OFF — ЦВЕТОКОРРЕКЦИЯ ВЫКЛ",1:"ICM_ON",2:"ICM_DEVCONST",3:"ICM_DEVMATCH (Windows ICM ВКЛ)",4:"ICM_HANDLES"}
INT={0:"default",1:"perceptual",2:"relative",3:"saturation",4:"absolute"}
print("=== ТЕКУЩЕЕ СОСТОЯНИЕ ДРАЙВЕРА 1410 ===")
print(" device        :", s["device"])
print(" разрешение    :", s["print_quality"], "dpi  (профиль хочет 576)")
print(" ICM метод     :", s["icm_method"], ICM.get(s["icm_method"],"?"), " <- профиль хочет ICM_OFF=0")
print(" ICM intent    :", s["icm_intent"], INT.get(s["icm_intent"],"?"))
print(" media type    :", s["media_type"])
print(" ориентация    :", s["orientation"], "(1=portrait 2=landscape)")
print(" бумага        :", s["paper_size"], f'{s["paper_length_tenths_mm"]/100:.0f}x{s["paper_width_tenths_mm"]/100:.0f} мм')
print(" цвет          :", s["color"], "(2=COLOR)")
print(" копий         :", s["copies"])
print(" driver_extra  :", s["driver_extra_len"], "байт приватных настроек Epson")
