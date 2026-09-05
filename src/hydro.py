"""River channel burning from NHDPlus HR.

DEMs record the water *surface*. We carve a bed below it:

* Water polygons (NHDWaterbody lakes/reservoirs, NHDArea stream/river areas)
  give bank-to-bank width for free. Their surface is the DEM itself, smoothed
  inside the polygon; depth comes from the highest stream order of any
  flowline passing through the polygon (a reservoir on an order-9 river is
  deep; a farm pond with no flowline is shallow).
* Flowlines outside any polygon (creeks) are buffered to a width keyed to
  stream order and carved to a matching depth.

Cross-section is a clipped parabola: depth * sqrt(min(1, d / taper)) where d is
distance from the bank, so banks slope in and the middle is flat.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from rasterio import features
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import box

# NHD feature types we treat as open water
WATERBODY_FTYPES = {390, 436, 493}        # LakePond, Reservoir, Estuary
AREA_FTYPES = {460, 445, 312, 336}         # StreamRiver, SeaOcean, BayInlet, CanalDitch


@dataclass
class HydroParams:
    min_order: int = 2                      # flowlines below this are ignored
    # channel width (m) and depth (m) by stream order for flowlines outside polygons
    width_by_order: dict = field(default_factory=lambda: {
        2: 4, 3: 8, 4: 14, 5: 24, 6: 40, 7: 70, 8: 120, 9: 200, 10: 300})
    depth_by_order: dict = field(default_factory=lambda: {
        2: 0.8, 3: 1.2, 4: 2.0, 5: 3.0, 6: 4.0, 7: 6.0, 8: 8.0, 9: 12.0, 10: 15.0})
    depth_scale: float = 1.0                # multiplies every depth
    polygon_taper_m: float = 60.0           # bank-to-full-depth distance inside polygons
    pond_depth_m: float = 2.0               # polygons with no flowline through them
    min_polygon_area_m2: float = 2000.0     # drop ponds smaller than this
    surface_smooth_m: float = 120.0         # window for the in-polygon surface estimate

    def width(self, order: int) -> float:
        return self.width_by_order[min(max(order, min(self.width_by_order)), max(self.width_by_order))]

    def depth(self, order: int) -> float:
        return self.depth_scale * self.depth_by_order[min(max(order, min(self.depth_by_order)), max(self.depth_by_order))]


@dataclass
class WaterLayers:
    mask: np.ndarray            # bool: any burned water
    polygon_mask: np.ndarray    # bool: polygon (wide) water only
    surface: np.ndarray         # float32: water surface elevation where mask, NaN elsewhere
    depth: np.ndarray           # float32: carve depth below surface (0 elsewhere)
    polygons: list              # (name, ftype, area_m2, order, surface_p10) for reporting


def _order_of(flow, geom) -> tuple[int, str | None]:
    """Highest stream order of any flowline through geom, and that flowline's name."""
    hits = flow[flow.intersects(geom)]
    if not len(hits):
        return 0, None
    top = hits.loc[hits["streamorde"].idxmax()]
    name = top.get("gnis_name")
    return int(top["streamorde"]), (str(name) if name is not None and str(name) != "nan" else None)


def _masked_mean(a: np.ndarray, mask: np.ndarray, size: int) -> np.ndarray:
    num = ndimage.uniform_filter(np.where(mask, a, 0.0).astype(np.float64), size=size)
    den = ndimage.uniform_filter(mask.astype(np.float64), size=size)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan).astype(np.float32)


def build_water_layers(dem: np.ndarray, transform: Affine, m_per_px: float, bbox,
                       flow, area, wb, p: HydroParams, *, progress=print) -> WaterLayers:
    h, w = dem.shape
    clip = box(*bbox.as_tuple())

    # ---- flowlines in extent, order >= min --------------------------------
    fl = flow[flow["streamorde"].fillna(0) >= p.min_order]
    fl = fl[fl.intersects(clip)].copy()
    fl["geometry"] = fl.geometry.intersection(clip)
    fl = fl[~fl.geometry.is_empty]

    # ---- water polygons ---------------------------------------------------
    polys = []
    for gdf, ok in ((wb, WATERBODY_FTYPES), (area, AREA_FTYPES)):
        if gdf is None or len(gdf) == 0:
            continue
        g = gdf[gdf["ftype"].isin(ok)]
        g = g[g.intersects(clip)]
        for _, row in g.iterrows():
            geom = row.geometry.intersection(clip)
            if geom.is_empty or geom.area < p.min_polygon_area_m2:
                continue
            order, river_name = _order_of(fl, geom)
            depth = p.depth(order) if order >= p.min_order else p.pond_depth_m * p.depth_scale
            name = row.get("gnis_name")
            name = str(name) if name is not None and str(name) != "nan" else None
            label = name or river_name or f"unnamed ftype {int(row['ftype'])}"
            polys.append((label, int(row["ftype"]), float(geom.area), order, depth, geom))
    progress(f"[hydro] {len(fl)} flowlines (order>={p.min_order}), {len(polys)} water polygons in extent")

    poly_mask = np.zeros((h, w), bool)
    poly_depth = np.zeros((h, w), np.float32)
    if polys:
        # rasterize deepest-last so overlapping polygons keep the deeper value
        shapes = [(g, d) for (_, _, _, _, d, g) in sorted(polys, key=lambda t: t[4])]
        poly_depth = features.rasterize(shapes, out_shape=(h, w), transform=transform,
                                        fill=0.0, dtype="float32", all_touched=False)
        poly_mask = poly_depth > 0
        # parabolic taper from the bank
        dist = ndimage.distance_transform_edt(poly_mask) * m_per_px
        taper = np.sqrt(np.clip(dist / p.polygon_taper_m, 0, 1)).astype(np.float32)
        poly_depth = poly_depth * taper

    # ---- creek channels from buffered flowlines outside polygons ----------
    line_mask = np.zeros((h, w), bool)
    line_depth = np.zeros((h, w), np.float32)
    if len(fl):
        depth_shapes, half_shapes = [], []
        for order, grp in fl.groupby("streamorde"):
            o = int(order)
            half = max(p.width(o) / 2.0, m_per_px * 0.5)   # at least ~1 px wide
            poly = grp.geometry.buffer(half, cap_style=2).union_all()
            depth_shapes.append((poly, p.depth(o)))
            half_shapes.append((poly, half / m_per_px))
        # rasterize shallow-first so the deeper (wider) order wins where they overlap
        depth_shapes.sort(key=lambda t: t[1])
        half_shapes.sort(key=lambda t: t[1])
        line_depth = features.rasterize(depth_shapes, out_shape=(h, w), transform=transform,
                                        fill=0.0, dtype="float32", all_touched=True)
        half_px = features.rasterize(half_shapes, out_shape=(h, w), transform=transform,
                                     fill=1.0, dtype="float32", all_touched=True)
        line_mask = (line_depth > 0) & ~poly_mask
        line_depth = np.where(line_mask, line_depth, 0.0).astype(np.float32)
        # taper creeks too: full depth along the centre line, shallower at the bank.
        # Distance is measured to the nearest non-water pixel; a 1-px creek gets ~0.7 depth.
        dist = ndimage.distance_transform_edt(line_mask | poly_mask)
        taper = np.sqrt(np.clip(dist / np.maximum(half_px, 1.0), 0, 1)).astype(np.float32)
        line_depth *= np.maximum(taper, np.float32(0.7))

    mask = poly_mask | line_mask
    depth = np.maximum(poly_depth, line_depth)

    # ---- water surface: DEM smoothed inside polygons, raw DEM on creeks ---
    surface = np.full((h, w), np.nan, np.float32)
    if poly_mask.any():
        size = max(3, int(round(p.surface_smooth_m / m_per_px)) | 1)
        sm = _masked_mean(dem, poly_mask, size)
        surface[poly_mask] = np.minimum(dem, sm)[poly_mask]
    surface[line_mask] = dem[line_mask]

    report = []
    for name, ftype, a, order, d, g in sorted(polys, key=lambda t: -t[2])[:12]:
        m = features.rasterize([(g, 1)], out_shape=(h, w), transform=transform, fill=0, dtype="uint8").astype(bool)
        vals = dem[m]
        p10 = float(np.percentile(vals, 10)) if vals.size else float("nan")
        report.append({"name": name, "ftype": ftype, "area_km2": round(a / 1e6, 3), "order": order,
                       "depth_m": round(d, 1), "surface_p10_m": round(p10, 2), "surface_p90_m": round(float(np.percentile(vals, 90)), 2) if vals.size else None})
    return WaterLayers(mask, poly_mask, surface, depth, report)


def burn_channels(dem: np.ndarray, layers: WaterLayers) -> np.ndarray:
    """Return a copy of dem with beds carved: surface - depth where water."""
    out = dem.copy()
    m = layers.mask
    bed = layers.surface[m] - layers.depth[m]
    out[m] = np.minimum(out[m], bed)
    return out
