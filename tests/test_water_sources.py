import numpy as np
import geopandas as gpd
import pytest
from rasterio.transform import from_origin
from shapely.geometry import LineString, Polygon

from src import spec
from src.geo import Site
from src.normalize import Vertical
from src.water_sources import propose_water_sources, WaterSourceParams, _snap_edge

N = spec.HEIGHTMAP_SIZE


def _site():
    return Site.from_center("t", 35.0456, -85.3097)


def _frames(site):
    bb = site.playable_bbox
    x0, y0, x1, y1 = bb.as_tuple()
    cx = (x0 + x1) / 2
    # big river: digitised N -> S (flow direction), enters at N edge, leaves at S edge
    river = LineString([(cx, y1 + 3000), (cx, y1 - 3000), (cx - 1000, y0 + 3000), (cx - 1000, y0 - 3000)])
    # creek entering from the W edge, order 4, digitised W -> E
    creek = LineString([(x0 - 2000, y0 + 4000), (x0 + 2500, y0 + 4200)])
    # headwater creek starting inside the map, order 4
    head = LineString([(x1 - 3000, y1 - 3000), (x1 - 1000, y1 - 5000)])
    # creek whose upstream end is inside the map and which flows OUT: still needs a source at
    # its upstream end (the water has to come from somewhere), placed inside, not on the edge
    leaver = LineString([(x0 + 3000, y0 + 1000), (x0 - 2000, y0 + 800)])
    # order-2 creek entering: below the stream order range
    tiny = LineString([(x0 - 1000, y1 - 1000), (x0 + 1000, y1 - 1200)])
    flow = gpd.GeoDataFrame({
        "gnis_name": ["Big River", "West Creek", "Head Creek", "Leaver Creek", "Tiny Creek"],
        "streamorde": [8, 4, 4, 5, 2], "flowdir": [1, 1, 1, 1, 1],
        "levelpathi": [1, 2, 3, 4, 5], "hydroseq": [10, 20, 30, 40, 50], "startflag": [0, 0, 1, 0, 0],
        "totdasqkm": [50000.0, 300.0, 40.0, 500.0, 5.0],
    }, geometry=[river, creek, head, leaver, tiny], crs=site.epsg)
    # lakes: one wholly inside (source), one crossed by the big river (no source), one too small
    lake_in = Polygon([(x0 + 2000, y1 - 2000), (x0 + 3000, y1 - 2000), (x0 + 3000, y1 - 3000), (x0 + 2000, y1 - 3000)])
    lake_river = Polygon([(cx - 300, y1 - 4000), (cx + 300, y1 - 4000), (cx + 300, y1 - 6000), (cx - 300, y1 - 6000)])
    pond = Polygon([(x0 + 500, y0 + 500), (x0 + 600, y0 + 500), (x0 + 600, y0 + 600), (x0 + 500, y0 + 600)])
    wb = gpd.GeoDataFrame({"gnis_name": ["Inner Lake", None, "Pond"], "ftype": [390, 436, 390]},
                          geometry=[lake_in, lake_river, pond], crs=site.epsg)
    area = gpd.GeoDataFrame({"gnis_name": [], "ftype": []}, geometry=[], crs=site.epsg)
    return flow, area, wb


def test_snap_edge():
    assert _snap_edge((3, 2000)) == (0, 2000, "W")
    assert _snap_edge((4090, 100)) == (4095, 100, "E")
    assert _snap_edge((2000, 2)) == (2000, 0, "N")
    assert _snap_edge((2000, 4094)) == (2000, 4095, "S")


def test_propose_water_sources():
    site = _site()
    bb = site.playable_bbox
    tf = from_origin(bb.minx, bb.maxy, 3.5, 3.5)
    flow, area, wb = _frames(site)
    dem = np.full((N, N), 200.0, np.float32)
    dem[:, :] += np.linspace(0, 20, N, dtype=np.float32)[:, None]      # slopes down to the north? no: row 0 = north, lowest
    water = np.zeros((N, N), bool)
    surface = np.full((N, N), np.nan, np.float32)
    v = Vertical(193.0, 63.0, 0.0, 1.0, 610.0, 5.0, 596.0, 0.0093)
    log = []
    ps = propose_water_sources(flow, area, wb, site, tf, surface_real=surface, dem_real=dem, water_mask=water,
                               vertical=v, p=WaterSourceParams(), progress=log.append)
    kinds = sorted(p.kind for p in ps)
    assert kinds == ["water.border_river_in", "water.border_river_out", "water.lake",
                     "water.stream", "water.stream", "water.stream"]
    by = {p.kind: [q for q in ps if q.kind == p.kind] for p in ps}
    rin, rout = by["water.border_river_in"][0], by["water.border_river_out"][0]
    assert rin.px[1] == 0 and rin.params["edge"] == "N" and "Big River" in rin.label
    assert rout.px[1] == N - 1 and rout.params["edge"] == "S"
    # level: north edge of the DEM is 200 real -> 70 in-game; south edge ~220 -> 90
    assert rin.elev_m == pytest.approx(70.0, abs=0.5)
    assert rout.elev_m == pytest.approx(90.0, abs=0.5)
    streams = sorted(by["water.stream"], key=lambda p: p.label)
    assert streams[0].label.startswith("Head Creek") and streams[0].params["entry"] == "headwater inside the map"
    assert streams[1].label.startswith("Leaver Creek") and streams[1].params["entry"].startswith("upstream end inside")
    assert 0 < streams[1].px[0] < N - 1 and 0 < streams[1].px[1] < N - 1        # inside, not snapped to an edge
    assert streams[2].label.startswith("West Creek") and streams[2].px[0] == 0 and streams[2].params["entry"] == "enters at the edge"
    assert streams[1].params["size"] == "large" and streams[0].params["size"] in ("small", "medium")
    lake = by["water.lake"][0]
    assert lake.label == "Inner Lake" and lake.params["area_km2"] == pytest.approx(1.0, abs=0.01)
    assert not any("Tiny" in p.label for p in ps)                               # order 2: below the stream range
    assert any("proposed 6 water sources" in l for l in log)


def test_parallel_channels_and_edge_nick_are_deduped():
    site = _site()
    bb = site.playable_bbox
    tf = from_origin(bb.minx, bb.maxy, 3.5, 3.5)
    x0, y0, x1, y1 = bb.as_tuple()
    cx = (x0 + x1) / 2
    # main river + two unnamed parallel side channels all leaving the S edge within 300 m
    main = LineString([(cx, y0 + 2000), (cx, y0 - 2000)])
    side1 = LineString([(cx + 150, y0 + 2000), (cx + 150, y0 - 2000)])
    side2 = LineString([(cx - 250, y0 + 2000), (cx - 250, y0 - 2000)])
    # a meander that nicks the E edge: out and back in within 80 m
    nick = LineString([(x1 - 500, cx), (x1 + 30, cx + 40), (x1 - 500, cx + 80)])
    flow = gpd.GeoDataFrame({"gnis_name": ["Big", None, None, "Nick"], "streamorde": [7, 7, 7, 7], "flowdir": [1] * 4,
                             "levelpathi": [1, 2, 3, 4], "hydroseq": [1, 1, 1, 1], "startflag": [0] * 4,
                             "totdasqkm": [5000.0, 5000.0, 5000.0, 5000.0]},
                            geometry=[main, side1, side2, nick], crs=site.epsg)
    empty = gpd.GeoDataFrame({"gnis_name": [], "ftype": []}, geometry=[], crs=site.epsg)
    v = Vertical(193.0, 63.0, 0.0, 1.0, 610.0, 5.0, 596.0, 0.0093)
    ps = propose_water_sources(flow, empty, empty, site, tf, surface_real=np.full((N, N), np.nan, np.float32),
                               dem_real=np.full((N, N), 200.0, np.float32), water_mask=np.zeros((N, N), bool),
                               vertical=v, p=WaterSourceParams(), progress=lambda *_: None)
    borders = [p for p in ps if p.kind.startswith("water.border_river")]
    assert len(borders) == 1 and borders[0].kind == "water.border_river_out" and "Big" in borders[0].label


def test_broken_river_reaches_collapse_to_one_stream_source():
    site = _site()
    bb = site.playable_bbox
    tf = from_origin(bb.minx, bb.maxy, 3.5, 3.5)
    cx, cy = site.cx, site.cy
    # one river in three open reaches (nodes not shared, e.g. culverts between), same drainage,
    # flowing east; DEM slopes down to the east so the western reach is the highest
    reaches = [LineString([(cx - 3000 + i * 2200, cy), (cx - 3000 + i * 2200 + 1500, cy)]) for i in range(3)]
    flow = gpd.GeoDataFrame({"gnis_name": [None] * 3, "streamorde": [5] * 3, "flowdir": [1] * 3, "levelpathi": [1, 2, 3],
                             "hydroseq": [1, 1, 1], "startflag": [0, 0, 0], "totdasqkm": [122.0] * 3},
                            geometry=reaches, crs=site.epsg)
    empty = gpd.GeoDataFrame({"gnis_name": [], "ftype": []}, geometry=[], crs=site.epsg)
    dem = np.tile(np.linspace(300, 100, N, dtype=np.float32), (N, 1))
    v = Vertical(193.0, 63.0, 0.0, 1.0, 610.0, 5.0, 596.0, 0.0093)
    ps = propose_water_sources(flow, empty, empty, site, tf, surface_real=np.full((N, N), np.nan, np.float32),
                               dem_real=dem, water_mask=np.zeros((N, N), bool), vertical=v, p=WaterSourceParams(),
                               progress=lambda *_: None)
    streams = [p for p in ps if p.kind == "water.stream"]
    assert len(streams) == 1
    assert streams[0].px[0] < N // 2 and "2 further reach" in streams[0].why      # the western (highest) one


def test_stream_cap_and_direction_confidence():
    site = _site()
    bb = site.playable_bbox
    tf = from_origin(bb.minx, bb.maxy, 3.5, 3.5)
    flow, area, wb = _frames(site)
    flow.loc[flow["gnis_name"] == "Big River", "flowdir"] = 0
    dem = np.full((N, N), 200.0, np.float32)
    v = Vertical(193.0, 63.0, 0.0, 1.0, 610.0, 5.0, 596.0, 0.0093)
    ps = propose_water_sources(flow, area, wb, site, tf, surface_real=np.full((N, N), np.nan, np.float32),
                               dem_real=dem, water_mask=np.zeros((N, N), bool), vertical=v,
                               p=WaterSourceParams(max_streams=1), progress=lambda *_: None)
    streams = [p for p in ps if p.kind == "water.stream"]
    assert len(streams) == 1 and streams[0].label.startswith("Leaver Creek")   # biggest drainage (500 km2) kept
    assert all("low" in p.params["direction_confidence"] for p in ps if p.kind.startswith("water.border_river"))
