"""Hydrography sources behind one interface (issue #17).

Both return the three frames the pipeline already consumes, in the site CRS:
  flow  : LineStrings with gnis_name, streamorde, ftype, fcode, lengthkm, totdasqkm, flowdir,
          levelpathi, hydroseq, dnhydroseq, startflag, terminalfl, culvert, intermittent
  area  : river/sea polygons with gnis_name, ftype (460 river, 445 sea, 312 bay, 336 canal)
  wb    : lake/reservoir polygons with gnis_name, ftype (390 lake, 436 reservoir, 493 estuary)

  NhdSource  NHDPlus HR (US), the existing fetchers plus the culvert/intermittent flags.
  OsmSource  OpenStreetMap via Overpass for geometry and names, HydroRIVERS for Strahler
             order and upstream area, the DEM for a flow-direction sanity check, and the DEM
             for the sea polygon where OSM has a coastline.
"""
from __future__ import annotations

import io
import json
import math
import time
import warnings
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import geopandas as gpd
import requests
from pyproj import Transformer
from shapely.geometry import LineString, Polygon, MultiPolygon, Point, box
from shapely.ops import polygonize, unary_union

from .cache import Cache, key_for
from .fetch import FetchError, fetch_nhd, FLOWLINE_FIELDS, USER_AGENT
from .geo import BBox, Site

OVERPASS_MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
HYDRORIVERS_URL = "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_{reg}_shp.zip"
TAG_ORDER = {"river": 3, "canal": 2, "stream": 1, "drain": 1, "ditch": 1}
# Drainage area (km²) -> NHDPlus-HR-equivalent stream order. HydroRIVERS Strahler orders run
# 2-3 lower than NHD HR for the same river because its network omits small headwaters, so
# every threshold in the pipeline (channel width, border-river cut-off) would misfire on them.
# Calibrated on Chattanooga: Tennessee 52,000 km² -> 9, S. Chickamauga 1,192 -> 6,
# N. Chickamauga 309 -> 5, Chattanooga Creek 134 -> 5, Lookout Creek 430 -> 5 (NHD says 6).
AREA_ORDER_KM2 = [(1.5, 1), (8, 2), (30, 3), (120, 4), (450, 5), (1800, 6), (7000, 7), (30000, 8)]


def order_from_area(km2: float) -> int:
    for limit, o in AREA_ORDER_KM2:
        if km2 < limit:
            return o
    return 9
FLOW_COLUMNS = ["permanent_identifier", "gnis_name", "streamorde", "ftype", "fcode", "lengthkm", "totdasqkm",
                "flowdir", "levelpathi", "hydroseq", "dnhydroseq", "startflag", "terminalfl", "culvert",
                "intermittent", "order_source", "geometry"]


class HydroSource:
    id = ""

    def fetch(self, site: Site, bbox: BBox, *, dem=None, progress=print):
        """-> (flow, area, wb) GeoDataFrames in site CRS. `dem` = (array, transform) of the
        same extent, used by OSM for direction checks and the sea polygon."""
        raise NotImplementedError


def _clean_name(v):
    return str(v) if v is not None and str(v) not in ("nan", "None", "") else None


# ----------------------------------------------------------------------------
# NHDPlus HR
# ----------------------------------------------------------------------------
class NhdSource(HydroSource):
    id = "nhd"

    def fetch(self, site, bbox, *, dem=None, progress=print):
        flow = fetch_nhd(site, bbox, "flowline", out_fields=FLOWLINE_FIELDS, progress=progress)
        area = fetch_nhd(site, bbox, "area", out_fields="permanent_identifier,gnis_name,ftype,fcode,areasqkm", required=False, progress=progress)
        wb = fetch_nhd(site, bbox, "waterbody", out_fields="permanent_identifier,gnis_name,ftype,fcode,areasqkm", required=False, progress=progress)
        flow = flow.copy()
        flow["culvert"] = flow["ftype"].fillna(0).astype(int).eq(428)            # NHD Pipeline / underground conduit
        flow["intermittent"] = flow["fcode"].fillna(0).astype(int).isin([46003, 46007])
        flow["order_source"] = "nhd"
        return flow, area, wb


# ----------------------------------------------------------------------------
# HydroRIVERS
# ----------------------------------------------------------------------------
def hydrorivers_region(lat: float, lon: float) -> str:
    """HydroSHEDS region code for a point (na, sa, eu, af, as, au, si, ar, gr)."""
    if lat >= 60 and lon >= 60:
        return "si"
    if lat >= 60 and -75 <= lon < -10:
        return "gr"
    if lat >= 60:
        return "ar"
    if -170 <= lon < -30:
        return "na" if lat >= 8 else "sa"            # 8 N ~ Panama/Colombia border
    if -30 <= lon < 60:
        return "eu" if lat >= 34 else "af" if lat < 34 and lon < 60 else "as"
    if lon >= 60 and lat >= -10:
        return "as"
    return "au"


def fetch_hydrorivers(region: str, wgs84_bbox: BBox, *, cache: Cache | None = None, progress=print) -> gpd.GeoDataFrame:
    cache = cache or Cache()
    d = cache.root / "hydrorivers"; d.mkdir(parents=True, exist_ok=True)
    path = d / f"HydroRIVERS_v10_{region}_shp.zip"
    if not path.exists():
        url = HYDRORIVERS_URL.format(reg=region)
        progress(f"[hydrorivers] downloading {url} (once per region, ~70-200 MB)")
        t0 = time.time()
        with requests.get(url, stream=True, timeout=600, headers={"User-Agent": USER_AGENT}) as r:
            if r.status_code != 200:
                raise FetchError(f"HydroRIVERS {url}: HTTP {r.status_code}")
            tmp = path.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            tmp.replace(path)
        progress(f"[hydrorivers] {path.stat().st_size / 1e6:.0f} MB in {time.time() - t0:.0f}s")
    with zipfile.ZipFile(path) as z:
        shp = [n for n in z.namelist() if n.lower().endswith(".shp")][0]
    g = gpd.read_file(f"zip://{path}!{shp}", bbox=wgs84_bbox.as_tuple())
    return g


# ----------------------------------------------------------------------------
# Overpass
# ----------------------------------------------------------------------------
def overpass_query(w: BBox) -> str:
    s, we, n, e = w.miny, w.minx, w.maxy, w.maxx
    bb = f"({s:.5f},{we:.5f},{n:.5f},{e:.5f})"
    return f"""[out:json][timeout:180];
(
  way["waterway"~"^(river|stream|canal|drain|ditch)$"]{bb};
  way["natural"="water"]{bb};
  relation["natural"="water"]{bb};
  way["natural"="coastline"]{bb};
);
out geom;"""


def fetch_overpass(w: BBox, *, cache: Cache | None = None, progress=print) -> dict:
    cache = cache or Cache()
    q = overpass_query(w)
    key = key_for("overpass", q)
    path = cache.get("osm", key, "json")
    if path is None:
        last = None
        for attempt in range(6):
            url = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
            try:
                t0 = time.time()
                r = requests.post(url, data={"data": q}, headers={"User-Agent": USER_AGENT}, timeout=240)
                if r.status_code == 200:
                    js = r.json()
                    if "elements" in js:
                        path = cache.put("osm", key, "json", r.content, {"url": url, "query": q, "elements": len(js["elements"])})
                        progress(f"[osm] {len(js['elements'])} elements from {url} ({time.time() - t0:.1f}s)")
                        break
                last = FetchError(f"Overpass {url}: HTTP {r.status_code} {r.text[:200]}")
            except (requests.RequestException, ValueError) as e:
                last = FetchError(f"Overpass {url}: {e}")
            time.sleep(3.0 * (attempt + 1))
        else:
            raise last
    else:
        progress(f"[osm] cache hit {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------
# OSM -> NHD-shaped frames
# ----------------------------------------------------------------------------
@dataclass
class OsmParams:
    match_m: float = 300.0          # HydroRIVERS match distance
    direction_min_drop_m: float = 0.5
    direction_flip_m: float = 1.0
    sea_level_m: float = 0.5        # DEM threshold for the sea polygon (EGM2008)
    min_polygon_m2: float = 500.0


def _coords_to_crs(geom_pts, tfm: Transformer):
    lons = [p["lon"] for p in geom_pts]; lats = [p["lat"] for p in geom_pts]
    xs, ys = tfm.transform(lons, lats)
    return list(zip(xs, ys))


def _relation_polygons(rel: dict, tfm: Transformer) -> list[Polygon]:
    outers, inners = [], []
    for m in rel.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        ring = _coords_to_crs(m["geometry"], tfm)
        if len(ring) < 2:
            continue
        (inners if m.get("role") == "inner" else outers).append(LineString(ring))
    if not outers:
        return []
    polys = list(polygonize(unary_union(outers)))
    if inners:
        holes = list(polygonize(unary_union(inners)))
        polys = [p.difference(unary_union(holes)) if holes else p for p in polys]
    out = []
    for p in polys:
        if isinstance(p, MultiPolygon):
            out += list(p.geoms)
        elif isinstance(p, Polygon) and not p.is_empty:
            out.append(p)
    return out


def _direction_check(line: LineString, dem, tfm_px, p: OsmParams) -> tuple[LineString, int, bool]:
    """Sample the DEM along the line; returns (line possibly reversed, flowdir, flipped)."""
    if dem is None:
        return line, 1, False
    arr, transform = dem
    n = max(len(line.coords), 8)
    pts = [line.interpolate(t, normalized=True) for t in np.linspace(0, 1, n)]
    vals = []
    for q in pts:
        c, r = ~transform * (q.x, q.y)
        c, r = int(c), int(r)
        if 0 <= r < arr.shape[0] and 0 <= c < arr.shape[1] and np.isfinite(arr[r, c]):
            vals.append(float(arr[r, c]))
        else:
            vals.append(np.nan)
    v = np.array(vals)
    k = max(2, n // 4)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # all-NaN slices off the raster
        head, tail = np.nanmedian(v[:k]), np.nanmedian(v[-k:])
    if not (np.isfinite(head) and np.isfinite(tail)):
        return line, 1, False
    drop = head - tail                      # positive = flows downhill in vertex order
    if drop < -p.direction_flip_m:
        return LineString(list(line.coords)[::-1]), 1, True
    if abs(drop) < p.direction_min_drop_m:
        return line, 0, False
    return line, 1, False


def _network(ways: list[dict]) -> None:
    """Assign levelpathi (component), hydroseq (1 at outlets, +1 upstream), startflag, terminalfl.
    Ways connect when one way's end touches another's start (directed) or any endpoints touch (component)."""
    snap = 2.0
    def key(pt):
        return (round(pt[0] / snap), round(pt[1] / snap))
    starts, ends = defaultdict(list), defaultdict(list)
    for i, w in enumerate(ways):
        c = list(w["geometry"].coords)
        starts[key(c[0])].append(i); ends[key(c[-1])].append(i)
    down = {i: [] for i in range(len(ways))}     # i -> ways downstream (their start == my end)
    up = {i: [] for i in range(len(ways))}
    for i, w in enumerate(ways):
        e = key(list(w["geometry"].coords)[-1])
        for j in starts.get(e, []):
            if j != i:
                down[i].append(j); up[j].append(i)
    # components (undirected, any endpoint touching)
    comp = [-1] * len(ways); cid = 0
    touch = defaultdict(set)
    for k, idx in list(starts.items()) + list(ends.items()):
        for a in idx:
            touch[a].update(starts.get(k, [])); touch[a].update(ends.get(k, []))
    for i in range(len(ways)):
        if comp[i] >= 0:
            continue
        cid += 1; dq = deque([i]); comp[i] = cid
        while dq:
            a = dq.popleft()
            for b in touch[a]:
                if comp[b] < 0:
                    comp[b] = cid; dq.append(b)
    # hydroseq via BFS upstream from outlets
    seq = [0] * len(ways)
    outlets = [i for i in range(len(ways)) if not down[i]]
    dq = deque((i, 1) for i in outlets)
    while dq:
        a, s = dq.popleft()
        if seq[a] and seq[a] <= s:
            continue
        seq[a] = s
        for b in up[a]:
            dq.append((b, s + 1))
    for i, w in enumerate(ways):
        w["levelpathi"] = comp[i]
        w["hydroseq"] = seq[i] or 1
        w["dnhydroseq"] = min((seq[j] for j in down[i]), default=0)
        w["startflag"] = int(not up[i])
        w["terminalfl"] = int(not down[i])


def _sea_polygon(dem, site_crs, p: OsmParams) -> list[Polygon]:
    """Connected components of DEM <= sea_level that touch the raster edge."""
    from rasterio import features
    from scipy import ndimage
    arr, transform = dem
    low = np.isfinite(arr) & (arr <= p.sea_level_m)
    if not low.any():
        return []
    lab, n = ndimage.label(low)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    edge.discard(0)
    if not edge:
        return []
    sea = np.isin(lab, list(edge))
    # downsample big rasters for polygonising
    step = max(1, arr.shape[0] // 1024)
    m = sea[::step, ::step].astype(np.uint8)
    t2 = transform * transform.__class__.scale(step, step)
    polys = [Polygon(s["coordinates"][0], s["coordinates"][1:]) for s, v in features.shapes(m, mask=m.astype(bool), transform=t2) if v == 1]
    return [q for q in polys if q.area > 1e5]


class OsmSource(HydroSource):
    id = "osm"

    def __init__(self, params: OsmParams | None = None, cache: Cache | None = None, use_hydrorivers: bool = True):
        self.p = params or OsmParams()
        self.cache = cache or Cache()
        self.use_hydrorivers = use_hydrorivers

    def frames_from_overpass(self, js: dict, site: Site, bbox: BBox, *, dem=None, hydrorivers: gpd.GeoDataFrame | None = None,
                             progress=print):
        tfm = Transformer.from_crs("EPSG:4326", site.crs, always_xy=True)
        clip = box(*bbox.as_tuple())
        ways, areas, wbs, coast = [], [], [], 0
        for e in js.get("elements", []):
            tags = e.get("tags", {})
            if e["type"] == "way" and e.get("geometry"):
                coords = _coords_to_crs(e["geometry"], tfm)
                if "waterway" in tags and len(coords) >= 2:
                    ways.append({"osm_id": e["id"], "tags": tags, "geometry": LineString(coords)})
                elif tags.get("natural") == "coastline":
                    coast += 1
                elif tags.get("natural") == "water" and len(coords) >= 4 and coords[0] == coords[-1]:
                    poly = Polygon(coords)
                    if poly.is_valid and poly.area >= self.p.min_polygon_m2:
                        (areas if tags.get("water") in ("river", "canal", "stream") else wbs).append((e["id"], tags, poly))
            elif e["type"] == "relation" and tags.get("natural") == "water":
                for poly in _relation_polygons(e, tfm):
                    if poly.area >= self.p.min_polygon_m2:
                        (areas if tags.get("water") in ("river", "canal", "stream") else wbs).append((e["id"], tags, poly))

        # direction + culvert/intermittent flags
        for w in ways:
            t = w["tags"]
            w["culvert"] = bool(t.get("tunnel") in ("culvert", "yes") or t.get("culvert") == "yes" or t.get("covered") == "yes")
            w["intermittent"] = t.get("intermittent") == "yes"
            w["geometry"], w["flowdir"], w["flipped"] = _direction_check(w["geometry"], dem, None, self.p)
        _network(ways)

        # HydroRIVERS join for order / drainage area
        for w in ways:
            w["streamorde"] = TAG_ORDER.get(w["tags"].get("waterway"), 1)
            w["totdasqkm"] = 0.0
            w["strahler"] = None
            w["discharge_cms"] = None
            w["order_source"] = "osm-tag"
        if hydrorivers is not None and len(hydrorivers) and ways:
            cols = [c for c in ("ORD_STRA", "UPLAND_SKM", "DIS_AV_CMS") if c in hydrorivers.columns]
            hr = hydrorivers.to_crs(site.crs)[cols + ["geometry"]].reset_index(drop=True)
            if "DIS_AV_CMS" not in hr.columns:
                hr["DIS_AV_CMS"] = np.nan
            # match on the way's mid-body (25 %, 50 %, 75 % points), not its nearest point: a tributary's
            # mouth is always close to the main river, but its body is not
            mids = gpd.GeoDataFrame({"i": range(len(ways))},
                                    geometry=[w["geometry"].interpolate(0.5, normalized=True) for w in ways], crs=site.crs)
            j = gpd.sjoin_nearest(mids, hr, how="left", max_distance=self.p.match_m, distance_col="d")
            j = j.sort_values("d").drop_duplicates("i")
            matched = {}
            for r in j.itertuples():
                if pd.isna(r.ORD_STRA):
                    continue
                seg = hr.geometry.iloc[int(r.index_right)]
                g = ways[int(r.i)]["geometry"]
                if all(g.interpolate(t, normalized=True).distance(seg) <= self.p.match_m for t in (0.25, 0.75)):
                    matched[int(r.i)] = (int(r.ORD_STRA), float(r.UPLAND_SKM), float(r.DIS_AV_CMS) if pd.notna(r.DIS_AV_CMS) else None)
            for i, (strahler, da, q) in matched.items():
                w = ways[i]
                # NHD-equivalent order from drainage area, never below the tag default
                w["streamorde"] = max(order_from_area(da), TAG_ORDER.get(w["tags"].get("waterway"), 1))
                w["strahler"], w["totdasqkm"], w["discharge_cms"] = strahler, da, q
                w["order_source"] = "hydrorivers-area"
            progress(f"[osm] {len(matched)}/{len(ways)} waterways matched to HydroRIVERS within {self.p.match_m:.0f} m "
                     f"(order from drainage area; Strahler kept in `strahler`)")

        flow = gpd.GeoDataFrame({
            "permanent_identifier": [f"osm-w{w['osm_id']}" for w in ways],
            "gnis_name": [_clean_name(w["tags"].get("name")) for w in ways],
            "streamorde": [w["streamorde"] for w in ways],
            "ftype": [336 if w["tags"].get("waterway") == "canal" else 460 for w in ways],
            "fcode": [46003 if w["intermittent"] else 46006 for w in ways],
            "lengthkm": [w["geometry"].length / 1000 for w in ways],
            "totdasqkm": [w["totdasqkm"] for w in ways],
            "flowdir": [w["flowdir"] for w in ways],
            "levelpathi": [w["levelpathi"] for w in ways],
            "hydroseq": [w["hydroseq"] for w in ways],
            "dnhydroseq": [w["dnhydroseq"] for w in ways],
            "startflag": [w["startflag"] for w in ways],
            "terminalfl": [w["terminalfl"] for w in ways],
            "culvert": [w["culvert"] for w in ways],
            "intermittent": [w["intermittent"] for w in ways],
            "order_source": [w["order_source"] for w in ways],
            "strahler": [w["strahler"] for w in ways],
            "discharge_cms": [w["discharge_cms"] for w in ways],
        }, geometry=[w["geometry"] for w in ways], crs=site.crs)

        def poly_frame(items, default_ftype):
            rows = []
            for oid, tags, poly in items:
                wt = tags.get("water", "")
                ft = {"river": 460, "stream": 460, "canal": 336, "reservoir": 436, "lagoon": 493, "lake": 390, "pond": 390}.get(wt, default_ftype)
                rows.append((f"osm-{oid}", _clean_name(tags.get("name")), ft, poly.area / 1e6, poly))
            return gpd.GeoDataFrame({"permanent_identifier": [r[0] for r in rows], "gnis_name": [r[1] for r in rows],
                                     "ftype": [r[2] for r in rows], "fcode": [0] * len(rows), "areasqkm": [r[3] for r in rows]},
                                    geometry=[r[4] for r in rows], crs=site.crs)
        area = poly_frame(areas, 460)
        wb = poly_frame(wbs, 390)
        if coast and dem is not None:
            seas = _sea_polygon(dem, site.crs, self.p)
            if seas:
                sea = gpd.GeoDataFrame({"permanent_identifier": [f"dem-sea-{i}" for i in range(len(seas))],
                                        "gnis_name": ["sea"] * len(seas), "ftype": [445] * len(seas), "fcode": [0] * len(seas),
                                        "areasqkm": [s.area / 1e6 for s in seas]}, geometry=seas, crs=site.crs)
                area = pd.concat([area, sea], ignore_index=True) if len(area) else sea
                progress(f"[osm] coastline present: {len(seas)} sea polygon(s) from the DEM (<= {self.p.sea_level_m} m)")
        nflip = sum(1 for w in ways if w["flipped"]); nunk = sum(1 for w in ways if w["flowdir"] == 0)
        progress(f"[osm] {len(flow)} waterways ({flow['culvert'].sum()} culverted, {flow['intermittent'].sum()} intermittent, "
                 f"{nflip} reversed by DEM, {nunk} direction unknown), {len(area)} river/sea areas, {len(wb)} water bodies")
        return flow, area, wb

    def fetch(self, site, bbox, *, dem=None, progress=print):
        w = site.bbox_wgs84(bbox)
        w = BBox(w.minx - 0.02, w.miny - 0.02, w.maxx + 0.02, w.maxy + 0.02)
        js = fetch_overpass(w, cache=self.cache, progress=progress)
        hr = None
        if self.use_hydrorivers:
            reg = hydrorivers_region(site.lat, site.lon)
            try:
                hr = fetch_hydrorivers(reg, w, cache=self.cache, progress=progress)
                progress(f"[hydrorivers] {len(hr)} segments in region {reg} for the extent")
            except FetchError as e:
                progress(f"[hydrorivers] WARNING: {e}; stream order falls back to OSM tags")
        flow, area, wb = self.frames_from_overpass(js, site, bbox, dem=dem, hydrorivers=hr, progress=progress)
        if len(flow) == 0:
            raise FetchError(f"OpenStreetMap has no waterways in the extent of {site.name} {w.as_str(3)}")
        return flow, area, wb


def resolve_hydro(site: Site, *, prefer: str | None = None, us: bool | None = None, progress=print) -> HydroSource:
    if prefer == "nhd":
        return NhdSource()
    if prefer == "osm":
        return OsmSource()
    if prefer:
        raise FetchError(f"unknown --hydro-source {prefer!r}; choose nhd or osm")
    src = NhdSource() if us else OsmSource()
    progress(f"[hydro] source: {src.id}")
    return src
