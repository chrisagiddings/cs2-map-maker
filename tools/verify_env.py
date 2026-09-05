"""Environment sanity check: versions, GDAL drivers, 16-bit PNG round-trip."""
import sys, os, tempfile
import numpy, scipy, rasterio, pyproj, geopandas, shapely, requests, PIL, matplotlib, imageio
import numpy as np
from PIL import Image
import rasterio.env

print("python   ", sys.version.split()[0])
for m in (numpy, scipy, rasterio, pyproj, geopandas, shapely, requests, PIL, matplotlib, imageio):
    print(f"{m.__name__:10s}", m.__version__)
print("GDAL     ", rasterio.__gdal_version__)
print("PROJ     ", pyproj.proj_version_str)
with rasterio.env.Env() as env:
    drv = set(env.drivers().keys())
need = ["GTiff", "PNG", "AAIGrid", "MEM", "VRT", "GeoJSON"]
print("drivers  ", {d: (d in drv) for d in need}, f"({len(drv)} total)")

# 16-bit PNG round trip: write with rasterio/GDAL, read back with Pillow (independent reader)
a = np.linspace(0, 65535, 256 * 256).reshape(256, 256).astype(np.uint16)
tf = os.path.join(tempfile.gettempdir(), "cs2_smoke16.png")
with rasterio.open(tf, "w", driver="PNG", width=256, height=256, count=1, dtype="uint16") as ds:
    ds.write(a, 1)
b = np.array(Image.open(tf))
ok = b.dtype == np.uint16 and b.shape == (256, 256) and b.max() == 65535 and np.array_equal(a, b)
print("png16 rt ", b.dtype, b.shape, int(b.min()), int(b.max()), "OK" if ok else "FAIL")
for f in (tf, tf + ".aux.xml"):
    if os.path.exists(f):
        os.unlink(f)
sys.exit(0 if ok else 1)
