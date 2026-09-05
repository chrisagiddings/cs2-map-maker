import numpy as np
import pandas as pd
import geopandas as gpd
import pytest
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import LineString

from src.geo import Site, BBox
from src.hydro_sources import OsmSource, OsmParams, _network, hydrorivers_region, overpass_query, resolve_hydro


def _site():
    return Site.from_center("t", 47.0105, 28.8638)


def _ll(site, x, y):
    lon, lat = Transformer.from_crs(site.crs, "EPSG:4326", always_xy=True).transform(x, y)
    return {"lat": lat, "lon": lon}


def _way(site, wid, pts, tags):
    return {"type": "way", "id": wid, "tags": tags, "geometry": [_ll(site, x, y) for x, y in pts]}


def test_region_lookup():
    assert hydrorivers_region(47.0, 28.9) == "eu"
    assert hydrorivers_region(35.0, -85.3) == "na"
    assert hydrorivers_region(4.7, -74.1) == "sa"
    assert hydrorivers_region(6.5, 3.4) == "af"
    assert hydrorivers_region(18.5, 73.9) == "as"
    assert hydrorivers_region(34.9, 135.8) == "as"
    assert hydrorivers_region(-33.9, 151.2) == "au"


def test_overpass_query_bbox_order():
    q = overpass_query(BBox(28.4, 46.7, 29.3, 47.3))
    assert "(46.70000,28.40000,47.30000,29.30000)" in q and 'way["natural"="coastline"]' in q


def test_network_topology():
    ways = [
        {"geometry": LineString([(0, 0), (100, 0)])},          # A: head
        {"geometry": LineString([(0, 50), (100, 0)])},         # B: head, joins A's end
        {"geometry": LineString([(100, 0), (200, 0)])},        # C: A+B -> C
        {"geometry": LineString([(200, 0), (300, 0)])},        # D: outlet
        {"geometry": LineString([(900, 900), (950, 950)])},    # E: separate component
    ]
    _network(ways)
    assert [w["levelpathi"] for w in ways[:4]] == [1, 1, 1, 1] and ways[4]["levelpathi"] == 2
    assert [w["hydroseq"] for w in ways[:4]] == [3, 3, 2, 1]
    assert [w["startflag"] for w in ways] == [1, 1, 0, 0, 1]
    assert [w["terminalfl"] for w in ways] == [0, 0, 0, 1, 1]
    assert ways[0]["dnhydroseq"] == 2 and ways[3]["dnhydroseq"] == 0


def test_frames_from_overpass_with_dem_and_hydrorivers():
    site = _site()
    bb = site.world_bbox
    cx, cy = site.cx, site.cy
    # DEM: slopes down toward +x (east) by 1 m per 100 m, at world resolution
    n = 200
    tf = from_origin(bb.minx, bb.maxy, bb.width / n, bb.height / n)
    xs = bb.minx + (np.arange(n) + 0.5) * bb.width / n
    dem = np.tile(300 - (xs - bb.minx) / 100, (n, 1)).astype(np.float32)
    js = {"elements": [
        # river drawn west -> east (downhill): kept
        _way(site, 1, [(cx - 5000, cy), (cx, cy)], {"waterway": "river", "name": "Bic"}),
        _way(site, 2, [(cx, cy), (cx + 5000, cy)], {"waterway": "river", "name": "Bic"}),
        # tributary drawn from the river node (low, east) up to the west (high): uphill in this DEM,
        # so it must be reversed to end at the river node (cx, cy), which is w1's end / w2's start
        _way(site, 3, [(cx, cy), (cx - 2000, cy + 3000)], {"waterway": "stream", "name": "Wrong Way"}),
        # culverted reach
        _way(site, 4, [(cx + 5000, cy), (cx + 6000, cy)], {"waterway": "river", "name": "Bic", "tunnel": "culvert"}),
        # intermittent ditch, flat DEM along y -> direction unknown
        _way(site, 5, [(cx + 1000, cy - 4000), (cx + 1000, cy - 3000)], {"waterway": "stream", "intermittent": "yes"}),
        # lake polygon (closed way) and a river polygon
        _way(site, 6, [(cx - 8000, cy - 8000), (cx - 7000, cy - 8000), (cx - 7000, cy - 7000), (cx - 8000, cy - 7000), (cx - 8000, cy - 8000)],
             {"natural": "water", "water": "lake", "name": "Lacul"}),
        _way(site, 7, [(cx - 1000, cy - 100), (cx + 1000, cy - 100), (cx + 1000, cy + 100), (cx - 1000, cy + 100), (cx - 1000, cy - 100)],
             {"natural": "water", "water": "river"}),
        # relation lake with an outer ring split into two ways
        {"type": "relation", "id": 8, "tags": {"natural": "water", "water": "reservoir", "name": "Rez"}, "members": [
            {"type": "way", "role": "outer", "geometry": [_ll(site, x, y) for x, y in [(cx + 8000, cy + 8000), (cx + 9000, cy + 8000), (cx + 9000, cy + 9000)]]},
            {"type": "way", "role": "outer", "geometry": [_ll(site, x, y) for x, y in [(cx + 9000, cy + 9000), (cx + 8000, cy + 9000), (cx + 8000, cy + 8000)]]},
        ]},
    ]}
    hr = gpd.GeoDataFrame({"ORD_STRA": [5, 5], "UPLAND_SKM": [1200.0, 1250.0]},
                          geometry=[LineString([(cx - 5000, cy + 50), (cx, cy + 50)]), LineString([(cx, cy + 50), (cx + 5000, cy + 50)])],
                          crs=site.crs).to_crs(4326)
    log = []
    flow, area, wb = OsmSource(OsmParams()).frames_from_overpass(js, site, bb, dem=(dem, tf), hydrorivers=hr, progress=log.append)
    f = flow.set_index("permanent_identifier")
    assert list(f.loc[["osm-w1", "osm-w2"], "streamorde"]) == [6, 6]
    assert f.loc["osm-w1", "totdasqkm"] == 1200.0
    assert f.loc["osm-w3", "streamorde"] == 1 and f.loc["osm-w3", "order_source"] == "osm-tag"
    # reversed tributary now ends at the river node
    assert f.loc["osm-w3", "geometry"].coords[-1] == pytest.approx((cx, cy)) and f.loc["osm-w3", "flowdir"] == 1
    assert bool(f.loc["osm-w4", "culvert"]) and not bool(f.loc["osm-w1", "culvert"])
    assert bool(f.loc["osm-w5", "intermittent"]) and f.loc["osm-w5", "fcode"] == 46003 and f.loc["osm-w5", "flowdir"] == 0
    # topology: w1 -> w2 -> w4 same component; w3 joins w1's end
    assert f.loc["osm-w1", "levelpathi"] == f.loc["osm-w4", "levelpathi"] == f.loc["osm-w3", "levelpathi"]
    assert f.loc["osm-w1", "hydroseq"] > f.loc["osm-w2", "hydroseq"] > f.loc["osm-w4", "hydroseq"]
    assert f.loc["osm-w1", "startflag"] == 1 and f.loc["osm-w4", "terminalfl"] == 1
    assert f.loc["osm-w1", "gnis_name"] == "Bic" and pd.isna(f.loc["osm-w5", "gnis_name"])
    # order comes from drainage area (1200 km² -> 6), Strahler kept separately
    assert f.loc["osm-w1", "streamorde"] == 6 and f.loc["osm-w1", "strahler"] == 5 and f.loc["osm-w1", "order_source"] == "hydrorivers-area"
    assert len(wb) == 2 and set(wb["ftype"]) == {390, 436} and "Rez" in set(wb["gnis_name"])
    assert len(area) == 1 and area.iloc[0]["ftype"] == 460 and area.iloc[0]["areasqkm"] == pytest.approx(0.4, abs=0.01)
    assert any("1 reversed by DEM" in l for l in log)


def test_frames_sea_from_coastline():
    site = Site.from_center("coast", 6.52, 3.38)
    bb = site.world_bbox
    n = 200
    tf = from_origin(bb.minx, bb.maxy, bb.width / n, bb.height / n)
    dem = np.full((n, n), 20.0, np.float32)
    dem[150:, :] = 0.0                                  # southern strip at sea level, touches the edge
    js = {"elements": [_way(site, 1, [(bb.minx, bb.miny + 12000), (bb.maxx, bb.miny + 12000)], {"natural": "coastline"}),
                       _way(site, 2, [(site.cx, site.cy + 9000), (site.cx, site.cy)], {"waterway": "river", "name": "R"})]}
    flow, area, wb = OsmSource().frames_from_overpass(js, site, bb, dem=(dem, tf), progress=lambda *_: None)
    assert len(area) == 1 and area.iloc[0]["ftype"] == 445 and area.iloc[0]["areasqkm"] > 700


def test_order_from_area_calibration():
    from src.hydro_sources import order_from_area
    assert order_from_area(52000) == 9 and order_from_area(1192) == 6 and order_from_area(309) == 5
    assert order_from_area(134) == 5 and order_from_area(430) == 5 and order_from_area(1134) == 6
    assert order_from_area(0.5) == 1 and order_from_area(20) == 3


def test_discharge_sizes_channels():
    from src.hydro import HydroParams, build_water_layers
    from src import spec
    site = _site(); bb = site.playable_bbox; N = spec.HEIGHTMAP_SIZE
    tf = from_origin(bb.minx, bb.maxy, 3.5, 3.5)
    dem = np.full((N, N), 100.0, np.float32)
    y = site.cy
    lines = [LineString([(site.cx - 6000, y + 3000), (site.cx + 6000, y + 3000)]),   # order 6, Q = 1.5 m3/s (dry basin)
             LineString([(site.cx - 6000, y - 3000), (site.cx + 6000, y - 3000)])]   # order 6, no discharge -> table width
    flow = gpd.GeoDataFrame({"gnis_name": ["Dry", "Table"], "streamorde": [6, 6], "discharge_cms": [1.5, None],
                             "culvert": [False, False], "intermittent": [False, False]}, geometry=lines, crs=site.crs)
    empty = gpd.GeoDataFrame({"gnis_name": [], "ftype": []}, geometry=[], crs=site.crs)
    L = build_water_layers(dem, tf, 3.5, bb, flow, empty, empty, HydroParams(), progress=lambda *_: None)
    r_dry = int((bb.maxy - (y + 3000)) / 3.5); r_tab = int((bb.maxy - (y - 3000)) / 3.5); c = N // 2
    w_dry = L.mask[r_dry - 20:r_dry + 20, c].sum(); w_tab = L.mask[r_tab - 20:r_tab + 20, c].sum()
    assert w_tab >= 10 and w_dry <= 4                       # 40 m table width vs ~6 m from Q
    assert L.depth[r_tab, c] > L.depth[r_dry, c] > 0


def test_resolve_hydro():
    assert resolve_hydro(_site(), us=False, progress=lambda *_: None).id == "osm"
    assert resolve_hydro(_site(), us=True, progress=lambda *_: None).id == "nhd"
    assert resolve_hydro(_site(), prefer="osm", us=True, progress=lambda *_: None).id == "osm"


def test_culverts_are_skipped_by_burn_and_sources():
    from src.hydro import HydroParams, build_water_layers
    from src.water_sources import propose_water_sources, WaterSourceParams
    from src.normalize import Vertical
    from src import spec
    site = _site()
    bb = site.playable_bbox
    N = spec.HEIGHTMAP_SIZE
    tf = from_origin(bb.minx, bb.maxy, 3.5, 3.5)
    dem = np.full((N, N), 100.0, np.float32)
    line = LineString([(site.cx - 8000, site.cy), (site.cx + 8000, site.cy)])
    flow = gpd.GeoDataFrame({"gnis_name": ["Poltva"], "streamorde": [6], "flowdir": [1], "levelpathi": [1], "hydroseq": [1],
                             "startflag": [0], "totdasqkm": [500.0], "culvert": [True], "intermittent": [False], "ftype": [460], "fcode": [46006]},
                            geometry=[line], crs=site.crs)
    empty = gpd.GeoDataFrame({"gnis_name": [], "ftype": []}, geometry=[], crs=site.crs)
    L = build_water_layers(dem, tf, 3.5, bb, flow, empty, empty, HydroParams(), progress=lambda *_: None)
    assert not L.mask.any()
    v = Vertical(100.0, 63.0, 0.0, 1.0, 610.0, 5.0, 596.0, 0.0093)
    ps = propose_water_sources(flow, empty, empty, site, tf, surface_real=L.surface, dem_real=dem, water_mask=L.mask,
                               vertical=v, p=WaterSourceParams(), progress=lambda *_: None)
    assert ps == []
