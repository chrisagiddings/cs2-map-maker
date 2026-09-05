import numpy as np
import pytest

from src import spec
from src.terrain import block_downsample, fill_nodata, terracing_fraction, deterrace, slope_percent, buildable_fraction
from src.normalize import plan_vertical, apply_vertical, to_uint16, NormalizeError


def test_block_downsample_nan_aware():
    a = np.arange(16, dtype=np.float32).reshape(4, 4)
    a[0, 0] = np.nan
    d = block_downsample(a, 2)
    assert d.shape == (2, 2)
    assert np.isclose(d[0, 0], np.nanmean([1, 4, 5]))
    assert np.isclose(d[1, 1], np.mean([10, 11, 14, 15]))


def test_fill_nodata_nearest():
    a = np.array([[1, np.nan, 3], [np.nan, np.nan, np.nan], [7, np.nan, 9]], np.float32)
    f, frac = fill_nodata(a)
    assert np.isfinite(f).all() and frac == pytest.approx(5 / 9)
    assert f[0, 1] in (1, 3) and f[1, 0] in (1, 7)


def test_terracing_detection_and_fix():
    rng = np.random.default_rng(0)
    y, x = np.mgrid[0:256, 0:256]
    # ~5% grade at 3.5 m/px -> one 1 m contour step every ~6 px, like a 10 m source DEM
    smooth = (x * 0.17 + y * 0.03).astype(np.float32)
    assert terracing_fraction(smooth) < 0.1
    terraced = np.round(smooth).astype(np.float32)     # integer-metre sources round, not floor
    assert terracing_fraction(terraced) > 0.99
    fixed = deterrace(terraced)
    # closer to the smooth truth than the terraced input
    assert np.abs(fixed - smooth).mean() < 0.6 * np.abs(terraced - smooth).mean()
    # a cliff must survive untouched
    cliff = np.where(x < 128, 0.0, 100.0).astype(np.float32)
    assert np.array_equal(deterrace(cliff), cliff)


def test_slope_and_buildable():
    y, x = np.mgrid[0:100, 0:100]
    a = (x * 0.35).astype(np.float32)          # 10% grade at 3.5 m/px
    s = slope_percent(a, 3.5)
    assert np.allclose(s[:, 1:-1], 10.0, atol=1e-3)
    assert buildable_fraction(s, threshold=10.01) == 1.0
    assert buildable_fraction(s, threshold=9.99) == 0.0
    water = np.zeros_like(a, bool); water[:, :50] = True
    flat = np.zeros_like(a)
    flat[:, 50:] = (x[:, 50:] * 1.0)             # steep on land
    s2 = slope_percent(flat, 3.5)
    assert buildable_fraction(s2, exclude=water) < 0.1 < buildable_fraction(s2)


def test_plan_vertical_auto_and_scale():
    play = np.array([[190.0, 400.0]], np.float32)
    world = np.array([[136.0, 727.0]], np.float32)
    v = plan_vertical(play, world, water_surface_real_m=193.0, exaggeration=1.0, floor_margin_m=5.0)
    # lowest terrain (136) sits 57 m below the water; sea level must be >= 62
    assert v.sea_level_m == 62.0
    assert v.union_min_m == pytest.approx(5.0)
    assert v.union_max_m == pytest.approx(727 - 193 + 62)
    assert v.height_scale_m == 610.0                   # ceil(596*1.02/10)*10
    assert v.height_scale_m >= spec.HEIGHT_SCALE_MIN_M
    u = to_uint16(apply_vertical(world, v), v.height_scale_m)
    assert u.dtype == np.uint16 and u.min() > 0 and u.max() < 65535
    with pytest.raises(NormalizeError, match="Minimum feasible"):
        plan_vertical(play, world, water_surface_real_m=193.0, sea_level_m=10.0)


def test_exaggeration_pivots_on_water_surface():
    play = np.array([[193.0, 293.0]], np.float32)
    world = np.array([[193.0, 293.0]], np.float32)
    v1 = plan_vertical(play, world, water_surface_real_m=193.0, exaggeration=1.0, sea_level_m=50)
    v2 = plan_vertical(play, world, water_surface_real_m=193.0, exaggeration=1.5, sea_level_m=50)
    a1, a2 = apply_vertical(play, v1), apply_vertical(play, v2)
    assert a1[0, 0] == a2[0, 0] == 50.0                 # water surface unchanged
    assert a2[0, 1] - 50 == pytest.approx(1.5 * (a1[0, 1] - 50))


def test_burn_synthetic_river():
    import geopandas as gpd
    from shapely.geometry import LineString, Polygon
    from rasterio.transform import from_origin
    from src.hydro import HydroParams, build_water_layers, burn_channels
    from src.geo import BBox

    n, mpp = 256, 3.5
    bbox = BBox(0, 0, n * mpp, n * mpp)
    tf = from_origin(0, n * mpp, mpp, mpp)
    dem = np.full((n, n), 100.0, np.float32)
    # a 140 m wide reservoir polygon across the middle with an order-9 flowline through it
    river = Polygon([(0, 380), (n * mpp, 380), (n * mpp, 520), (0, 520)])
    wb = gpd.GeoDataFrame({"gnis_name": ["Big"], "ftype": [436], "fcode": [43600], "areasqkm": [1.0]}, geometry=[river], crs=32616)
    area = gpd.GeoDataFrame({"gnis_name": [], "ftype": [], "fcode": [], "areasqkm": []}, geometry=[], crs=32616)
    lines = [LineString([(0, 450), (n * mpp, 450)]), LineString([(450, 0), (450, 380)])]
    flow = gpd.GeoDataFrame({"gnis_name": ["Big", "Creek"], "streamorde": [9, 3], "ftype": [460, 460], "fcode": [46006, 46006]},
                            geometry=lines, crs=32616)
    p = HydroParams()
    L = build_water_layers(dem, tf, mpp, bbox, flow, area, wb, p, progress=lambda *_: None)
    out = burn_channels(dem, L)
    row = n - int(450 / mpp)                             # row of the river centreline
    assert out[row, 128] == pytest.approx(100 - p.depth(9), abs=0.05)    # full depth mid-river
    bank = n - int(382 / mpp)
    assert 100 - p.depth(9) < out[bank, 128] < 100                        # tapered near bank
    creek_col = int(450 / mpp)
    creek_row = n - int(100 / mpp)
    assert out[creek_row, creek_col] < 100                                # creek carved
    assert out[creek_row, creek_col] >= 100 - p.depth(3) - 1e-3
    assert out[creek_row, creek_col + 20] == 100                          # dry land untouched
    assert L.polygons[0]["order"] == 9 and L.polygons[0]["depth_m"] == p.depth(9)
