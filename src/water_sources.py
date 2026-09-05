"""Recommend CS2 water sources from NHDPlus HR (issue #12, supersedes #5).

CS2 map-editor source types and how we map them:
  Border River   - a flowline of order >= border_min_order crosses the playable edge.
                   Level = the water surface at that crossing (from the un-burned DEM,
                   not the single reference surface). Direction from NHD digitizing
                   order (network flowlines are digitized downstream; `flowdir` == 1).
  Stream         - an order stream_orders creek that enters the playable area at the
                   edge, or starts inside it. Rate hint from NHD drainage area (totdasqkm).
  Constant Level - a lake/reservoir polygon >= lake_min_km2 wholly inside the playable
                   area that is not on a border river.
  Border Sea     - an NHDArea SeaOcean/BayInlet/Estuary polygon touching the edge.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from shapely.geometry import box, LineString, MultiLineString, Point
from shapely.ops import linemerge

from . import spec
from .guide import Placement
from .hydro import WATERBODY_FTYPES, AREA_FTYPES

SEA_FTYPES = {445, 312, 493}       # SeaOcean, BayInlet, Estuary


@dataclass
class WaterSourceParams:
    border_min_order: int = 6
    stream_orders: tuple[int, int] = (4, 5)
    max_streams: int = 8
    lake_min_km2: float = 0.5
    merge_m: float = 150.0          # crossings of the same river closer than this are one crossing
    cluster_m: float = 400.0        # parallel channels crossing within this, same direction -> one source
    loop_m: float = 150.0           # an in/out pair closer than this is a meander nicking the edge: drop both
    sample_px: int = 12             # neighbourhood radius for the surface level at a point


def _to_px(transform, x: float, y: float) -> tuple[int, int]:
    col, row = ~transform * (x, y)
    n = spec.HEIGHTMAP_SIZE
    return int(min(max(col, 0), n - 1)), int(min(max(row, 0), n - 1))


def _snap_edge(px: tuple[int, int]) -> tuple[int, int, str]:
    """Move a near-edge pixel onto the edge; return (col, row, side)."""
    c, r = px
    n = spec.HEIGHTMAP_SIZE
    d = {"W": c, "E": n - 1 - c, "N": r, "S": n - 1 - r}
    side = min(d, key=d.get)
    if side == "W": c = 0
    elif side == "E": c = n - 1
    elif side == "N": r = 0
    else: r = n - 1
    return c, r, side


def _level_at(px, surface_cs2: np.ndarray, dem_cs2: np.ndarray, water: np.ndarray, radius: int) -> float:
    """Water surface (in-game m) near a pixel: median of the smoothed surface over
    water pixels in the neighbourhood, else the lowest DEM value there."""
    c, r = px
    n = spec.HEIGHTMAP_SIZE
    r0, r1, c0, c1 = max(r - radius, 0), min(r + radius + 1, n), max(c - radius, 0), min(c + radius + 1, n)
    w = water[r0:r1, c0:c1]
    s = surface_cs2[r0:r1, c0:c1]
    vals = s[w & np.isfinite(s)]
    if vals.size:
        return float(np.median(vals))
    return float(np.nanmin(dem_cs2[r0:r1, c0:c1]))


def _lines(geom):
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    return []


def _crossings(line: LineString, ring, poly, step_m: float = 30.0):
    """(point, inflow) for each place the line crosses the playable boundary,
    using vertex order as flow direction."""
    inter = line.intersection(ring)
    pts = [inter] if isinstance(inter, Point) else [g for g in getattr(inter, "geoms", []) if isinstance(g, Point)]
    out = []
    for pt in pts:
        d = line.project(pt)
        down = line.interpolate(min(d + step_m, line.length))
        up = line.interpolate(max(d - step_m, 0.0))
        inflow = poly.contains(down) and not poly.contains(up)
        outflow = poly.contains(up) and not poly.contains(down)
        if inflow or outflow:
            out.append((pt, inflow))
    return out


def _dedupe_crossings(found: list, p: "WaterSourceParams") -> list:
    """Parallel channels (OSM side arms, canals, braids) cross the edge within a few hundred
    metres of each other and all carry the main river's order. Keep one crossing per direction
    per cluster (named first, then the biggest drainage), and drop in/out pairs closer than
    `loop_m` (a meander that nicks the boundary)."""
    if len(found) < 2:
        return found
    def name_ok(row):
        g = row.get("gnis_name")
        return g is not None and str(g) != "nan"
    kept = []
    used = [False] * len(found)
    for i, (pt, inflow, row) in enumerate(found):
        if used[i]:
            continue
        cluster = [j for j in range(len(found)) if not used[j] and found[j][1] == inflow
                   and found[j][0].distance(pt) <= p.cluster_m]
        for j in cluster:
            used[j] = True
        best = max(cluster, key=lambda j: (name_ok(found[j][2]), float(found[j][2].get("totdasqkm") or 0)))
        kept.append(found[best])
    out = []
    for i, (pt, inflow, row) in enumerate(kept):
        partner = any(k != i and kept[k][1] != inflow and kept[k][0].distance(pt) <= p.loop_m for k in range(len(kept)))
        if not partner:
            out.append((pt, inflow, row))
    return out


def propose_water_sources(flow, area, wb, site, transform, *, surface_real: np.ndarray, dem_real: np.ndarray,
                          water_mask: np.ndarray, vertical, p: WaterSourceParams | None = None,
                          progress=print) -> list[Placement]:
    p = p or WaterSourceParams()
    bb = site.playable_bbox
    poly = box(*bb.as_tuple())
    ring = poly.exterior
    to_cs2 = lambda a: (a - vertical.water_surface_real_m) * vertical.exaggeration + vertical.sea_level_m
    surface_cs2 = to_cs2(surface_real)
    dem_cs2 = to_cs2(dem_real)

    def level(px):
        return round(_level_at(px, surface_cs2, dem_cs2, water_mask, p.sample_px), 1)

    def name_of(row):
        g = row.get("gnis_name")
        return str(g) if g is not None and str(g) != "nan" else f"unnamed order-{int(row['streamorde'])} stream"

    placements: list[Placement] = []
    fl = flow[flow["streamorde"].notna()].copy()
    if "culvert" in fl.columns:
        fl = fl[~fl["culvert"].fillna(False).astype(bool)]      # underground rivers get no source
    fl["streamorde"] = fl["streamorde"].astype(int)
    near = fl[fl.intersects(ring)]

    # ---- border rivers -------------------------------------------------------
    rivers = near[near["streamorde"] >= p.border_min_order]
    found = []     # (point, inflow, row)
    for _, row in rivers.iterrows():
        for ln in _lines(row.geometry):
            for pt, inflow in _crossings(ln, ring, poly):
                key = row.get("levelpathi")
                if any(k == key and inflow == fi and pt.distance(fp) < p.merge_m for fp, fi, k in
                       [(f[0], f[1], f[2].get("levelpathi")) for f in found]):
                    continue
                found.append((pt, inflow, row))
    found = _dedupe_crossings(found, p)
    for pt, inflow, row in found:
        c, r, side = _snap_edge(_to_px(transform, pt.x, pt.y))
        lv = level((c, r))
        conf = "high" if int(row.get("flowdir") or 0) == 1 else "low (flow direction uninitialised in NHD)"
        placements.append(Placement(
            "water.border_river_in" if inflow else "water.border_river_out", (c, r), elev_m=lv,
            label=f"{name_of(row)} {'inflow' if inflow else 'outflow'} ({side} edge)",
            why=f"order-{int(row['streamorde'])} flowline crosses the {side} edge; set level {lv:.1f} m; "
                f"drainage {float(row.get('totdasqkm') or 0):,.0f} km²; direction confidence {conf}",
            params={"flow": "in" if inflow else "out", "order": int(row["streamorde"]), "edge": side,
                    "level_m": lv, "drainage_km2": float(row.get("totdasqkm") or 0),
                    "direction_confidence": conf, "nhd_levelpath": row.get("levelpathi")}))
    big_paths = set(rivers["levelpathi"].dropna().tolist())

    # ---- streams: order-4/5 creeks entering at the edge or starting inside -----
    lo, hi = p.stream_orders
    creeks = fl[(fl["streamorde"] >= lo) & (fl["streamorde"] <= hi) & fl.intersects(poly)]
    candidates = []
    for lp, grp in creeks.groupby("levelpathi"):
        if lp in big_paths:
            continue
        inside = grp[grp.intersects(poly)]
        if inside.empty:
            continue
        top = inside.loc[inside["hydroseq"].idxmax()]          # most upstream segment in the map
        start = Point(_lines(top.geometry)[0].coords[0])
        entry = None
        if poly.contains(start) and int(top.get("startflag") or 0) == 1:
            entry, how = start, "headwater inside the map"
        else:
            for ln in _lines(top.geometry):
                for pt, inflow in _crossings(ln, ring, poly):
                    if inflow:
                        entry, how = pt, "enters at the edge"
            if entry is None and poly.contains(start):
                entry, how = start, "upstream end inside the map (reaches this order here)"
        if entry is None:
            continue
        candidates.append((float(top.get("totdasqkm") or 0), entry, top, how))
    candidates.sort(key=lambda t: -t[0])
    skipped = len(candidates) - min(len(candidates), p.max_streams)
    biggest = candidates[0][0] if candidates else 1.0
    for da, pt, row, how in candidates[: p.max_streams]:
        c, r = _to_px(transform, pt.x, pt.y)
        side = ""
        if how == "enters at the edge":
            c, r, side = _snap_edge((c, r))
        lv = level((c, r))
        rel = da / biggest if biggest else 0
        size = "large" if rel > 0.5 else "medium" if rel > 0.15 else "small"
        placements.append(Placement(
            "water.stream", (c, r), elev_m=lv,
            label=f"{name_of(row)}" + (f" ({side} edge)" if side else ""),
            why=f"order-{int(row['streamorde'])} creek {how}; drainage {da:,.0f} km² -> {size} flow "
                f"({rel:.0%} of the largest stream here)",
            params={"order": int(row["streamorde"]), "drainage_km2": da, "relative_rate": round(rel, 2),
                    "size": size, "entry": how, "nhd_levelpath": row.get("levelpathi")}))
    if skipped:
        progress(f"[water] {skipped} smaller creeks not listed (cap {p.max_streams}); raise max_streams to include them")

    # ---- lakes wholly inside, not on a border river ---------------------------
    for gdf, ok in ((wb, WATERBODY_FTYPES), (area, AREA_FTYPES - SEA_FTYPES)):
        if gdf is None or len(gdf) == 0:
            continue
        g = gdf[gdf["ftype"].isin(ok) & gdf.within(poly)]
        for _, row in g.iterrows():
            km2 = row.geometry.area / 1e6
            if km2 < p.lake_min_km2:
                continue
            through = fl[fl.intersects(row.geometry)]
            if (through["streamorde"] >= p.border_min_order).any():
                continue                                           # river reservoir: fed by border rivers
            rp = row.geometry.representative_point()
            c, r = _to_px(transform, rp.x, rp.y)
            lv = level((c, r))
            nm = row.get("gnis_name"); nm = str(nm) if nm is not None and str(nm) != "nan" else "unnamed lake"
            placements.append(Placement(
                "water.lake", (c, r), elev_m=lv, label=nm,
                why=f"{km2:.2f} km² water body wholly inside the map; constant level {lv:.1f} m",
                params={"area_km2": round(km2, 3), "level_m": lv, "ftype": int(row["ftype"])}))

    # ---- border sea ------------------------------------------------------------
    if area is not None and len(area):
        seas = area[area["ftype"].isin(SEA_FTYPES) & area.intersects(ring)]
        for _, row in seas.iterrows():
            seg = row.geometry.intersection(ring)
            if seg.is_empty:
                continue
            mid = seg.interpolate(0.5, normalized=True) if hasattr(seg, "interpolate") else seg.representative_point()
            c, r, side = _snap_edge(_to_px(transform, mid.x, mid.y))
            lv = level((c, r))
            placements.append(Placement(
                "water.border_sea", (c, r), elev_m=lv, label=f"sea ({side} edge)",
                why=f"sea/estuary polygon touches the {side} edge over {seg.length / 1000:.1f} km; level {lv:.1f} m",
                params={"edge": side, "level_m": lv, "edge_length_km": round(seg.length / 1000, 2)}))

    kinds = {}
    for pl in placements:
        kinds[pl.kind] = kinds.get(pl.kind, 0) + 1
    progress(f"[water] proposed {len(placements)} water sources: " + ", ".join(f"{k.split('.')[1]} x{v}" for k, v in sorted(kinds.items())))
    return placements
