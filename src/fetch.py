"""Data fetchers: USGS 3DEP elevation and NHDPlus HR hydrography.

Every network call goes through `Cache`; re-running with the same site never
re-downloads. Every fetcher raises `FetchError` with a real message when a
service returns nothing usable for the requested extent.
"""
from __future__ import annotations

import io
import json
import math
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.transform import from_origin
from rasterio.errors import NotGeoreferencedWarning

from .cache import Cache, key_for
from .geo import BBox, Site

USER_AGENT = "cs2-maps/0.1 (real-world terrain -> Cities: Skylines II heightmaps)"

DEM_SERVICE = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
NHD_SERVICE = "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer"
NHD_LAYERS = {"flowline": 3, "area": 8, "waterbody": 9}
# one field list for every caller: the cache key includes it
FLOWLINE_FIELDS = ("permanent_identifier,gnis_name,streamorde,ftype,fcode,lengthkm,totdasqkm,"
                   "flowdir,levelpathi,hydroseq,dnhydroseq,startflag,terminalfl")

DEM_NODATA = -999999.0     # value we ask the server to use for nodata
DEM_TILE_PX = 2048         # server max is 8000; smaller tiles fail/retry more gracefully
DEM_MAX_TILE_PX = 8000


class FetchError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _get(session: requests.Session, url: str, params: dict, *, timeout: int = 300,
         retries: int = 4, expect: str | None = None) -> requests.Response:
    last = None
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            ctype = r.headers.get("Content-Type", "")
            if r.status_code == 200 and (expect is None or expect in ctype):
                return r
            # ArcGIS returns HTTP 200 + JSON on error; surface that message
            if "json" in ctype:
                try:
                    err = r.json().get("error", {})
                    last = FetchError(f"{url}: {err.get('message')} {err.get('details')}")
                except ValueError:
                    last = FetchError(f"{url}: HTTP {r.status_code}, unparseable JSON")
            else:
                last = FetchError(f"{url}: HTTP {r.status_code}, Content-Type {ctype!r}")
        except requests.RequestException as e:
            last = FetchError(f"{url}: {e}")
        time.sleep(2.0 * (attempt + 1))
    raise last


# ----------------------------------------------------------------------------
# 3DEP elevation
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class DemSourceInfo:
    finest_ground_m: float          # approx ground pixel size of finest source
    finest_name: str
    datasets: list[str]


def dem_source_info(site: Site, bbox: BBox, session: requests.Session | None = None) -> DemSourceInfo:
    """Ask the 3DEP catalog what source datasets cover `bbox` and how fine they are."""
    session = session or _session()
    w = site.bbox_wgs84(bbox)
    r = _get(session, f"{DEM_SERVICE}/query", {
        "f": "json", "geometry": w.as_str(6), "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        "outFields": "Name,LowPS,Category,Dataset_ID", "returnGeometry": "false",
    }, expect="json")
    feats = [f["attributes"] for f in r.json().get("features", []) if f["attributes"].get("Category") == 1]
    if not feats:
        raise FetchError(f"3DEP catalog lists no elevation datasets intersecting {w.as_str(4)}")
    # LowPS is in Web Mercator metres; scale by cos(lat) for ground metres.
    k = math.cos(math.radians(site.lat))
    best = min(feats, key=lambda a: a["LowPS"])
    names = sorted({a["Name"] for a in feats})
    return DemSourceInfo(round(best["LowPS"] * k, 2), best["Name"], names)


def _tile_grid(bbox: BBox, m_per_px: float, tile_px: int) -> tuple[int, int, list[tuple[int, int, BBox, int, int]]]:
    """Split bbox into tiles of at most tile_px. Returns (width_px, height_px, tiles)
    where each tile is (row0, col0, tile_bbox, tile_w, tile_h) in output pixels."""
    w_px = int(round(bbox.width / m_per_px))
    h_px = int(round(bbox.height / m_per_px))
    if abs(w_px * m_per_px - bbox.width) > 1e-6 or abs(h_px * m_per_px - bbox.height) > 1e-6:
        raise FetchError(f"bbox {bbox.width}x{bbox.height} m is not a whole number of {m_per_px} m pixels")
    tiles = []
    for r0 in range(0, h_px, tile_px):
        th = min(tile_px, h_px - r0)
        for c0 in range(0, w_px, tile_px):
            tw = min(tile_px, w_px - c0)
            tb = BBox(bbox.minx + c0 * m_per_px, bbox.maxy - (r0 + th) * m_per_px,
                      bbox.minx + (c0 + tw) * m_per_px, bbox.maxy - r0 * m_per_px)
            tiles.append((r0, c0, tb, tw, th))
    return w_px, h_px, tiles


def _fetch_dem_tile(session, cache: Cache, site: Site, tb: BBox, tw: int, th: int) -> np.ndarray:
    params = {
        "f": "image", "bbox": tb.as_str(3), "bboxSR": site.epsg, "imageSR": site.epsg,
        "size": f"{tw},{th}", "format": "tiff", "pixelType": "F32",
        "noData": DEM_NODATA, "noDataInterpretation": "esriNoDataMatchAny",
        "interpolation": "RSP_BilinearInterpolation",
    }
    key = key_for("3dep", params)
    path = cache.get("dem_tiles", key, "tif")
    if path is None:
        r = _get(session, f"{DEM_SERVICE}/exportImage", params, expect="tiff")
        path = cache.put("dem_tiles", key, "tif", r.content, {"url": f"{DEM_SERVICE}/exportImage", "params": params})
    with rasterio.open(path) as ds:
        a = ds.read(1).astype(np.float32)
        if ds.shape != (th, tw):
            raise FetchError(f"3DEP tile {key}: expected {tw}x{th}, got {ds.shape[1]}x{ds.shape[0]}")
        # Server-side nodata may come back as our value, the dataset's, or NaN
        nd = ds.nodata
    a[(a <= DEM_NODATA + 1) | (a < -1e4) | (a > 1e5)] = np.nan
    if nd is not None and not np.isnan(nd):
        a[a == nd] = np.nan
    return a


def fetch_dem(site: Site, bbox: BBox, m_per_px: float, *, label: str, cache: Cache | None = None,
              session: requests.Session | None = None, tile_px: int = DEM_TILE_PX,
              progress=print) -> tuple[np.ndarray, rasterio.Affine, Path]:
    """Fetch a 3DEP DEM for `bbox` (in the site's UTM CRS) at exactly `m_per_px`.

    Returns (array float32 with NaN nodata, affine transform, path of cached mosaic).
    The mosaic GeoTIFF is cached too, so the tile loop only runs once per site/extent.
    """
    cache = cache or Cache()
    session = session or _session()
    if tile_px > DEM_MAX_TILE_PX:
        raise FetchError(f"tile_px {tile_px} exceeds server maximum {DEM_MAX_TILE_PX}")
    w_px, h_px, tiles = _tile_grid(bbox, m_per_px, tile_px)
    mkey = key_for("3dep-mosaic", site.epsg, bbox.as_tuple(), m_per_px, DEM_NODATA, "bilinear")
    transform = from_origin(bbox.minx, bbox.maxy, m_per_px, m_per_px)
    mpath = cache.get("dem", mkey, "tif")
    if mpath is None:
        progress(f"[dem:{label}] {w_px}x{h_px} px @ {m_per_px} m/px in EPSG:{site.epsg}, {len(tiles)} tiles")
        mosaic = np.full((h_px, w_px), np.nan, np.float32)
        for i, (r0, c0, tb, tw, th) in enumerate(tiles, 1):
            t0 = time.time()
            mosaic[r0:r0 + th, c0:c0 + tw] = _fetch_dem_tile(session, cache, site, tb, tw, th)
            progress(f"[dem:{label}]   tile {i}/{len(tiles)} ok ({time.time() - t0:.1f}s)")
        valid = np.isfinite(mosaic)
        if not valid.any():
            raise FetchError(f"3DEP returned only nodata for {label} extent {bbox.as_str(0)} (EPSG:{site.epsg})")
        frac = valid.mean()
        if frac < 0.98:
            progress(f"[dem:{label}] WARNING: only {frac:.1%} of pixels have data")
        mpath = cache.path("dem", mkey, "tif")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.open(mpath, "w", driver="GTiff", width=w_px, height=h_px, count=1,
                               dtype="float32", crs=site.crs, transform=transform,
                               nodata=DEM_NODATA, compress="deflate", predictor=3, tiled=True) as ds:
                out = np.where(valid, mosaic, DEM_NODATA).astype(np.float32)
                ds.write(out, 1)
        mpath.with_suffix(".json").write_text(json.dumps({
            "source": DEM_SERVICE, "label": label, "epsg": site.epsg, "bbox_utm": bbox.as_tuple(),
            "m_per_px": m_per_px, "tiles": len(tiles), "valid_fraction": float(frac)}, indent=1))
    else:
        progress(f"[dem:{label}] cache hit {mpath.name}")
    with rasterio.open(mpath) as ds:
        a = ds.read(1).astype(np.float32)
        a[a == ds.nodata] = np.nan
        return a, ds.transform, mpath


# ----------------------------------------------------------------------------
# NHDPlus HR hydrography
# ----------------------------------------------------------------------------
def fetch_nhd(site: Site, bbox: BBox, layer: str, *, cache: Cache | None = None,
              session: requests.Session | None = None, where: str = "1=1",
              out_fields: str = "*", progress=print, required: bool = True):
    """Fetch NHDPlus HR features intersecting `bbox` as a GeoDataFrame in the site CRS.

    Pages through the 2000-record limit. Raises FetchError if `required` and the
    query returns zero features.
    """
    import geopandas as gpd

    if layer not in NHD_LAYERS:
        raise FetchError(f"unknown NHD layer {layer!r}; choose from {sorted(NHD_LAYERS)}")
    cache = cache or Cache()
    session = session or _session()
    lid = NHD_LAYERS[layer]
    base = {
        "f": "geojson", "where": where, "outFields": out_fields,
        "geometry": bbox.as_str(3), "geometryType": "esriGeometryEnvelope",
        "inSR": site.epsg, "outSR": site.epsg, "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "true", "geometryPrecision": 2,
    }
    key = key_for("nhdplus-hr", lid, base)
    path = cache.get("nhd", key, "geojson")
    if path is None:
        feats, offset = [], 0
        while True:
            r = _get(session, f"{NHD_SERVICE}/{lid}/query", dict(base, resultOffset=offset, resultRecordCount=2000), expect="json")
            page = r.json()
            if "error" in page:
                raise FetchError(f"NHD {layer}: {page['error']}")
            got = page.get("features", [])
            feats.extend(got)
            progress(f"[nhd:{layer}] page @{offset}: {len(got)} features")
            if not page.get("exceededTransferLimit") or not got:
                break
            offset += len(got)
        fc = {"type": "FeatureCollection", "features": feats,
              "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{site.epsg}"}}}
        path = cache.put("nhd", key, "geojson", json.dumps(fc).encode(),
                         {"url": f"{NHD_SERVICE}/{lid}/query", "params": base, "features": len(feats)})
    else:
        progress(f"[nhd:{layer}] cache hit {path.name}")
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(site.epsg)
    if required and len(gdf) == 0:
        raise FetchError(f"NHDPlus HR {layer} returned zero features for {bbox.as_str(0)} (EPSG:{site.epsg})")
    return gdf
