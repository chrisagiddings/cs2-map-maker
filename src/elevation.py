"""Elevation sources behind one interface (issue #16).

    source = resolve_elevation(site, bbox)          # finest DTM covering it, else finest DSM
    dem, transform, meta = source.fetch(site, bbox, m_per_px, label="playable")

Sources:
  ThreeDepSource  USGS 3DEP ImageServer (US). Server reprojects to UTM; 1 m LiDAR where present.
  LocalSource     user-supplied GeoTIFFs (national DTMs behind registration walls: GSI Japan,
                  TINITALY, EU-DTM ...) declared in data/local/sources.json.
  Cop30Source     Copernicus GLO-30 DSM, global, 1 arc-second, public AWS bucket, no key.
                  Missing tiles are ocean and become 0 m (EGM2008).

Resampling policy (recorded in meta["resample"]):
  native <= target/2   fetch at target/2, block-average         (no invented detail)
  native <= target     fetch/warp at target, bilinear
  native  > target     warp at target with cubic spline          (smooth upsample; QA states the
                                                                  true native resolution)
"""
from __future__ import annotations

import json
import math
import time
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.merge import merge as rio_merge
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling

from . import spec
from .cache import Cache, key_for
from .fetch import FetchError, dem_source_info, fetch_dem, _get, _session
from .geo import BBox, Site
from .terrain import block_downsample

LOCAL_SOURCES_FILE = Path(__file__).resolve().parent.parent / "data" / "local" / "sources.json"
COP30_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"


@dataclass
class DemMeta:
    source: str                # short id: 3dep | cop30 | local:<name>
    product: str               # dataset / tile name
    kind: str                  # dtm | dsm
    native_m: float
    vertical_datum: str
    resample: str              # policy branch applied
    oversample: int            # 1 or 2 (block-average factor)
    m_per_px: float
    missing_tiles: list | None = None
    datasets: list | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class ElevationSource:
    id: str = ""
    kind: str = "dtm"
    vertical_datum: str = ""

    def covers(self, site: Site, bbox: BBox) -> bool:
        raise NotImplementedError

    def native_m(self, site: Site, bbox: BBox) -> float:
        raise NotImplementedError

    def fetch(self, site: Site, bbox: BBox, m_per_px: float, *, label: str,
              oversample: str | int = "auto", progress=print) -> tuple[np.ndarray, rasterio.Affine, DemMeta]:
        raise NotImplementedError

    def describe(self, site: Site, bbox: BBox) -> str:
        return f"{self.id} ({self.kind}, ~{self.native_m(site, bbox):g} m)"


def _policy(native: float, target: float, oversample: str | int) -> tuple[str, int, float]:
    """-> (policy name, block factor, fetch resolution)."""
    if oversample != "auto":
        f = int(oversample)
        return ("oversample-forced" if f > 1 else "direct"), f, target / f
    if native <= target / 2:
        return "fetch-fine-then-block-average", 2, target / 2
    if native <= target:
        return "direct-bilinear", 1, target
    return "upsample-cubic-spline", 1, target


# ----------------------------------------------------------------------------
# USGS 3DEP
# ----------------------------------------------------------------------------
class ThreeDepSource(ElevationSource):
    id = "3dep"
    kind = "dtm"
    vertical_datum = "NAVD88"

    def __init__(self):
        self._info = {}

    def _catalog(self, site: Site, bbox: BBox):
        k = (site.epsg, bbox.as_tuple())
        if k not in self._info:
            try:
                self._info[k] = dem_source_info(site, bbox)
            except FetchError:
                self._info[k] = None
        return self._info[k]

    def covers(self, site, bbox) -> bool:
        return self._catalog(site, bbox) is not None

    def native_m(self, site, bbox) -> float:
        info = self._catalog(site, bbox)
        return info.finest_ground_m if info else float("inf")

    def fetch(self, site, bbox, m_per_px, *, label, oversample="auto", progress=print):
        info = self._catalog(site, bbox)
        if info is None:
            raise FetchError(f"3DEP has no data for {label} extent of {site.name}")
        policy, f, res = _policy(info.finest_ground_m, m_per_px, oversample)
        # 3DEP resamples server-side (bilinear) to whatever we ask; cubic upsampling is moot there
        a, tf, _ = fetch_dem(site, bbox, res, label=label, progress=progress)
        if f > 1:
            a = block_downsample(a, f)
            tf = from_origin(bbox.minx, bbox.maxy, m_per_px, m_per_px)
        return a, tf, DemMeta(self.id, info.finest_name, self.kind, info.finest_ground_m, self.vertical_datum,
                              policy, f, m_per_px, datasets=info.datasets)


# ----------------------------------------------------------------------------
# shared: warp a set of GeoTIFFs onto the site grid
# ----------------------------------------------------------------------------
def warp_to_grid(files: list[Path], dst_crs, bbox: BBox, m_per_px: float, *, resampling: Resampling,
                 src_nodata: float | None = None, fill_uncovered: float | None = None,
                 lonlat_pad: float = 0.02) -> np.ndarray:
    """Mosaic `files` (any CRS) and warp onto the bbox grid at m_per_px. Pixels no file
    covers become `fill_uncovered` (or NaN). Nodata inside files becomes NaN."""
    w = int(round(bbox.width / m_per_px)); h = int(round(bbox.height / m_per_px))
    dst_tf = from_origin(bbox.minx, bbox.maxy, m_per_px, m_per_px)
    dst = np.full((h, w), np.nan, np.float32)
    if not files:
        return dst if fill_uncovered is None else np.full((h, w), fill_uncovered, np.float32)
    dss = [rasterio.open(f) for f in files]
    try:
        # merge in the files' own CRS (all tiles of one product share it), clipped to the bbox
        from pyproj import Transformer
        tfm = Transformer.from_crs(dst_crs, dss[0].crs, always_xy=True)
        xs, ys = tfm.transform([bbox.minx, bbox.maxx, bbox.maxx, bbox.minx], [bbox.miny, bbox.miny, bbox.maxy, bbox.maxy])
        pad = lonlat_pad if dss[0].crs.is_geographic else lonlat_pad * 111_000
        bounds = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        finest = min(min(abs(d.res[0]), abs(d.res[1])) for d in dss)
        nd = src_nodata if src_nodata is not None else dss[0].nodata
        mosaic, mtf = rio_merge(dss, bounds=bounds, res=finest, nodata=nd, resampling=Resampling.bilinear)
        mosaic = mosaic[0].astype(np.float32)
        if nd is not None:
            mosaic[mosaic == nd] = np.nan
        mosaic[(mosaic < -1000) | (mosaic > 20000)] = np.nan
        covered = np.isfinite(mosaic)
        if fill_uncovered is not None:
            # uncovered = no tile at all (ocean for COP30); voids inside tiles stay NaN for fill_nodata
            # we cannot distinguish the two after merge, so treat every NaN as uncovered here and let
            # the caller decide; COP30 voids are rare (ocean tiles are the common case)
            mosaic = np.where(covered, mosaic, np.float32(fill_uncovered))
        src_crs = dss[0].crs
    finally:
        for d in dss:
            d.close()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        reproject(mosaic, dst, src_transform=mtf, src_crs=src_crs, src_nodata=np.nan,
                  dst_transform=dst_tf, dst_crs=dst_crs, dst_nodata=np.nan, resampling=resampling)
    return dst


def _resampling_for(native: float, target: float) -> tuple[Resampling, str]:
    if native > target:
        return Resampling.cubic_spline, "upsample-cubic-spline"
    if native > target / 2:
        return Resampling.bilinear, "direct-bilinear"
    return Resampling.average, "downsample-average"


# ----------------------------------------------------------------------------
# Copernicus GLO-30 (AWS open data)
# ----------------------------------------------------------------------------
class Cop30Source(ElevationSource):
    id = "cop30"
    kind = "dsm"
    vertical_datum = "EGM2008"

    def __init__(self, cache: Cache | None = None):
        self.cache = cache or Cache()

    @staticmethod
    def tile_name(lat: int, lon: int) -> str:
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"

    @staticmethod
    def tiles_for(w: BBox) -> list[tuple[int, int]]:
        lat0, lat1 = math.floor(w.miny), math.floor(w.maxy - 1e-9)
        lon0, lon1 = math.floor(w.minx), math.floor(w.maxx - 1e-9)
        return [(la, lo) for la in range(lat0, lat1 + 1) for lo in range(lon0, lon1 + 1)]

    def covers(self, site, bbox) -> bool:
        return -90 <= site.lat <= 90

    def native_m(self, site, bbox) -> float:
        return 30.0

    def _tile(self, lat: int, lon: int, session, progress) -> Path | None:
        name = self.tile_name(lat, lon)
        d = self.cache.root / "cop30"; d.mkdir(parents=True, exist_ok=True)
        path, missing = d / f"{name}.tif", d / f"{name}.missing"
        if path.exists() and path.stat().st_size > 0:
            return path
        if missing.exists():
            return None
        url = f"{COP30_BASE}/{name}/{name}.tif"
        t0 = time.time()
        r = session.get(url, timeout=300)
        if r.status_code == 404:
            missing.write_text(json.dumps({"url": url, "status": 404, "note": "no tile: ocean"}))
            progress(f"[cop30] {name}: no tile (ocean)")
            return None
        if r.status_code != 200:
            raise FetchError(f"COP30 {url}: HTTP {r.status_code}")
        tmp = path.with_suffix(".part"); tmp.write_bytes(r.content); tmp.replace(path)
        path.with_suffix(".json").write_text(json.dumps({"url": url, "bytes": len(r.content)}))
        from .cache import SESSION
        SESSION["bytes"] += len(r.content); SESSION["files"] += 1
        progress(f"[cop30] {name}: {len(r.content) / 1e6:.1f} MB ({time.time() - t0:.1f}s)")
        return path

    def fetch(self, site, bbox, m_per_px, *, label, oversample="auto", progress=print):
        w = site.bbox_wgs84(bbox)
        tiles = self.tiles_for(BBox(w.minx - 0.01, w.miny - 0.01, w.maxx + 0.01, w.maxy + 0.01))
        mkey = key_for("cop30-warp", site.epsg, bbox.as_tuple(), m_per_px)
        mpath = self.cache.get("dem", mkey, "tif")
        resampling, policy = _resampling_for(30.0, m_per_px)
        missing: list[str] = []
        if mpath is None:
            session = _session()
            files = []
            for la, lo in tiles:
                p = self._tile(la, lo, session, progress)
                if p is None:
                    missing.append(self.tile_name(la, lo))
                else:
                    files.append(p)
            if not files:
                raise FetchError(f"COP30 has no tiles at all for {label} extent of {site.name} "
                                 f"({len(tiles)} tiles, all missing): open ocean?")
            progress(f"[cop30:{label}] warping {len(files)} tile(s) -> {site.epsg} @ {m_per_px} m ({policy})")
            a = warp_to_grid(files, site.crs, bbox, m_per_px, resampling=resampling, fill_uncovered=0.0)
            mpath = self.cache.path("dem", mkey, "tif")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", NotGeoreferencedWarning)
                with rasterio.open(mpath, "w", driver="GTiff", width=a.shape[1], height=a.shape[0], count=1,
                                   dtype="float32", crs=site.crs, transform=from_origin(bbox.minx, bbox.maxy, m_per_px, m_per_px),
                                   nodata=-999999.0, compress="deflate", predictor=3, tiled=True) as ds:
                    ds.write(np.where(np.isfinite(a), a, -999999.0).astype(np.float32), 1)
            mpath.with_suffix(".json").write_text(json.dumps({"source": "cop30", "tiles": [self.tile_name(*t) for t in tiles],
                                                             "missing": missing, "policy": policy, "m_per_px": m_per_px}))
        else:
            progress(f"[cop30:{label}] cache hit {mpath.name}")
            try:
                missing = json.loads(mpath.with_suffix(".json").read_text()).get("missing", [])
            except Exception:
                missing = []
        with rasterio.open(mpath) as ds:
            a = ds.read(1).astype(np.float32); a[a == ds.nodata] = np.nan
            tf = ds.transform
        return a, tf, DemMeta(self.id, "Copernicus GLO-30", self.kind, 30.0, self.vertical_datum, policy, 1, m_per_px,
                              missing_tiles=missing or None, datasets=[self.tile_name(*t) for t in tiles])


# ----------------------------------------------------------------------------
# Local GeoTIFFs (national DTMs the user downloaded)
# ----------------------------------------------------------------------------
class LocalSource(ElevationSource):
    """One entry of data/local/sources.json:
        {"name": "gsi_5m", "files": ["data/local/gsi/*.tif"], "kind": "dtm",
         "native_m": 5, "vertical_datum": "JGD2011 orthometric", "nodata": -9999}
    """
    def __init__(self, entry: dict, root: Path):
        self.entry = entry
        self.id = f"local:{entry['name']}"
        self.kind = entry.get("kind", "dtm")
        self.vertical_datum = entry.get("vertical_datum", "unknown")
        self._native = float(entry["native_m"])
        self.files: list[Path] = []
        for pat in entry["files"]:
            self.files += sorted((root / pat).parent.glob((root / pat).name)) if any(c in pat for c in "*?[") else [root / pat]
        self.files = [f for f in self.files if f.exists()]
        self._bounds = None

    def _union_bounds_in(self, crs):
        if self._bounds is None:
            from rasterio.warp import transform_bounds
            bs = []
            for f in self.files:
                with rasterio.open(f) as ds:
                    bs.append(transform_bounds(ds.crs, crs, *ds.bounds))
            self._bounds = bs
        return self._bounds

    def covers(self, site, bbox) -> bool:
        if not self.files:
            return False
        from shapely.geometry import box
        from shapely.ops import unary_union
        u = unary_union([box(*b) for b in self._union_bounds_in(site.crs)])
        return u.contains(box(*bbox.as_tuple()))

    def native_m(self, site, bbox) -> float:
        return self._native

    def fetch(self, site, bbox, m_per_px, *, label, oversample="auto", progress=print):
        resampling, policy = _resampling_for(self._native, m_per_px)
        progress(f"[{self.id}:{label}] warping {len(self.files)} file(s) -> {site.epsg} @ {m_per_px} m ({policy})")
        a = warp_to_grid(self.files, site.crs, bbox, m_per_px, resampling=resampling, src_nodata=self.entry.get("nodata"))
        return a, from_origin(bbox.minx, bbox.maxy, m_per_px, m_per_px), DemMeta(
            self.id, self.entry.get("product", self.entry["name"]), self.kind, self._native, self.vertical_datum,
            policy, 1, m_per_px, datasets=[f.name for f in self.files])


def local_sources(path: Path = LOCAL_SOURCES_FILE) -> list[LocalSource]:
    if not path.exists():
        return []
    entries = json.loads(path.read_text(encoding="utf-8"))
    return [LocalSource(e, path.parent.parent.parent) for e in entries]


# ----------------------------------------------------------------------------
# resolver
# ----------------------------------------------------------------------------
def all_sources() -> list[ElevationSource]:
    return [*local_sources(), ThreeDepSource(), Cop30Source()]


def resolve_elevation(site: Site, bbox: BBox, *, prefer: str | None = None,
                      sources: list[ElevationSource] | None = None, progress=print) -> ElevationSource:
    """Finest DTM that covers bbox; else finest DSM. `prefer` forces a source id."""
    sources = sources if sources is not None else all_sources()
    if prefer:
        for s in sources:
            if s.id == prefer:
                if not s.covers(site, bbox):
                    raise FetchError(f"--dem-source {prefer} does not cover {site.name}")
                return s
        raise FetchError(f"unknown --dem-source {prefer!r}; known: {[s.id for s in sources]}")
    covering = [s for s in sources if s.covers(site, bbox)]
    if not covering:
        raise FetchError(f"no elevation source covers {site.name} {bbox.as_str(0)}")
    dtms = [s for s in covering if s.kind == "dtm"]
    pool = dtms or covering
    best = min(pool, key=lambda s: s.native_m(site, bbox))
    progress(f"[elevation] {site.name}: {best.describe(site, bbox)}"
             + (f"; also available: {', '.join(s.describe(site, bbox) for s in covering if s is not best)}" if len(covering) > 1 else ""))
    return best
