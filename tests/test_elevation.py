import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling

from src.elevation import (Cop30Source, ElevationSource, LocalSource, _policy, _resampling_for,
                           resolve_elevation, warp_to_grid)
from src.fetch import FetchError
from src.geo import BBox, Site


class Fake(ElevationSource):
    def __init__(self, id, kind, native, covers=True):
        self.id, self.kind, self._n, self._c = id, kind, native, covers
    def covers(self, site, bbox): return self._c
    def native_m(self, site, bbox): return self._n


def test_resolver_prefers_finest_dtm_then_dsm():
    site = Site.from_center("x", 47.0, 28.8)
    bb = site.playable_bbox
    srcs = [Fake("cop30", "dsm", 30), Fake("3dep", "dtm", 0.8, covers=False), Fake("local:gsi", "dtm", 10)]
    assert resolve_elevation(site, bb, sources=srcs, progress=lambda *_: None).id == "local:gsi"
    srcs[1]._c = True
    assert resolve_elevation(site, bb, sources=srcs, progress=lambda *_: None).id == "3dep"
    assert resolve_elevation(site, bb, sources=[Fake("cop30", "dsm", 30)], progress=lambda *_: None).id == "cop30"
    assert resolve_elevation(site, bb, sources=srcs, prefer="cop30", progress=lambda *_: None).id == "cop30"
    with pytest.raises(FetchError, match="no elevation source"):
        resolve_elevation(site, bb, sources=[Fake("a", "dtm", 1, covers=False)], progress=lambda *_: None)
    with pytest.raises(FetchError, match="unknown --dem-source"):
        resolve_elevation(site, bb, sources=srcs, prefer="nope", progress=lambda *_: None)


def test_policy_branches():
    assert _policy(0.8, 3.5, "auto") == ("fetch-fine-then-block-average", 2, 1.75)
    assert _policy(3.0, 3.5, "auto") == ("direct-bilinear", 1, 3.5)
    assert _policy(10.0, 3.5, "auto") == ("upsample-cubic-spline", 1, 3.5)
    assert _policy(0.8, 3.5, 1) == ("direct", 1, 3.5)
    assert _resampling_for(30, 3.5) == (Resampling.cubic_spline, "upsample-cubic-spline")
    assert _resampling_for(30, 14) == (Resampling.cubic_spline, "upsample-cubic-spline")
    assert _resampling_for(10, 14) == (Resampling.bilinear, "direct-bilinear")
    assert _resampling_for(1, 14) == (Resampling.average, "downsample-average")


def test_cop30_tile_naming():
    assert Cop30Source.tile_name(47, 28) == "Copernicus_DSM_COG_10_N47_00_E028_00_DEM"
    assert Cop30Source.tile_name(-33, -70) == "Copernicus_DSM_COG_10_S33_00_W070_00_DEM"
    assert Cop30Source.tile_name(4, -74) == "Copernicus_DSM_COG_10_N04_00_W074_00_DEM"
    tiles = Cop30Source.tiles_for(BBox(28.48, 46.75, 29.25, 47.27))
    assert tiles == [(46, 28), (46, 29), (47, 28), (47, 29)]
    assert Cop30Source.tiles_for(BBox(28.1, 47.1, 28.9, 47.9)) == [(47, 28)]


def test_warp_to_grid_from_geographic_tiles(tmp_path):
    # two 1-degree-ish tiles in EPSG:4326 with a known planar surface z = 100 + 50*(lon-28) + 30*(lat-47)
    def write(path, lon0, lat0, n=120, res=1 / 120):
        lons = lon0 + (np.arange(n) + 0.5) * res
        lats = lat0 + 1 - (np.arange(n) + 0.5) * res
        z = 100 + 50 * (lons[None, :] - 28) + 30 * (lats[:, None] - 47)
        with rasterio.open(path, "w", driver="GTiff", width=n, height=n, count=1, dtype="float32", crs="EPSG:4326",
                           transform=from_origin(lon0, lat0 + 1, res, res), nodata=-32767.0) as ds:
            ds.write(z.astype(np.float32), 1)
    a, b = tmp_path / "a.tif", tmp_path / "b.tif"
    write(a, 28, 46); write(b, 28, 47)
    site = Site.from_center("x", 47.0, 28.5)
    bb = BBox(site.cx - 3000, site.cy - 3000, site.cx + 3000, site.cy + 3000)
    out = warp_to_grid([a, b], site.crs, bb, 30.0, resampling=Resampling.bilinear)
    assert out.shape == (200, 200) and np.isfinite(out).all()
    # check against the analytic surface at the grid centre
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(site.crs, "EPSG:4326", always_xy=True).transform(site.cx, site.cy)
    expect = 100 + 50 * (lon - 28) + 30 * (lat - 47)
    assert abs(out[100, 100] - expect) < 0.5
    # a missing tile region: fill_uncovered gives 0 (ocean) instead of NaN
    bb2 = BBox(site.cx + 60000, site.cy, site.cx + 63000, site.cy + 3000)     # east of both tiles
    out2 = warp_to_grid([a, b], site.crs, bb2, 30.0, resampling=Resampling.bilinear, fill_uncovered=0.0)
    assert np.nanmax(np.abs(out2)) == 0.0


def test_local_source_covers_and_fetch(tmp_path):
    n, res = 400, 10.0
    site = Site.from_center("x", 47.0, 28.5)
    x0, y1 = site.cx - 2000, site.cy + 2000
    z = np.full((n, n), 250.0, np.float32)
    f = tmp_path / "dtm.tif"
    with rasterio.open(f, "w", driver="GTiff", width=n, height=n, count=1, dtype="float32", crs=site.crs,
                       transform=from_origin(x0, y1, res, res), nodata=-9999.0) as ds:
        ds.write(z, 1)
    cfg = tmp_path / "data" / "local" / "sources.json"
    cfg.parent.mkdir(parents=True)
    entry = {"name": "t", "files": [str(f)], "kind": "dtm", "native_m": 10, "vertical_datum": "x", "nodata": -9999}
    src = LocalSource(entry, tmp_path)
    inside = BBox(site.cx - 1000, site.cy - 1000, site.cx + 1000, site.cy + 1000)
    assert src.covers(site, inside)
    assert not src.covers(site, site.playable_bbox)              # 4 km tile cannot cover 14 km
    a, tf, meta = src.fetch(site, inside, 3.5, label="t", progress=lambda *_: None)
    assert a.shape == (571, 571) and np.allclose(a, 250.0, atol=0.01)      # 2000 m / 3.5 m = 571.4 -> 571
    assert meta.kind == "dtm" and meta.resample == "upsample-cubic-spline" and meta.native_m == 10
