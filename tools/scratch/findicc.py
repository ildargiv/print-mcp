import sys; sys.path.insert(0,".")
from print_mcp import icc
m = icc.find_profile("1410 Sublim TKANI photo bumaga", [])
print("найдено:", len(m))
for p in m:
    print("  path:", p.path)
    print("  desc:", p.description, "| class:", p.device_class, "| space:", p.color_space)
