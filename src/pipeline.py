"""End-to-end: cached raw data -> two uint16 heightmaps + stats.

Order of operations (each is a Stage 3 rule):
  1. metric CRS + exact px size   (done at fetch: server exports in UTM; here we block-average the oversample)
  2. de-terrace if the source is integer-metre
  3. burn river channels from NHD on both maps at their own resolution
  4. choose the reference water surface, sea level, exaggeration, ONE height scale
  5. world centre := playable downsampled (contract), quantize, verify
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict

import numpy as np

from . import spec
from .export import downsample_playable_to_world, worldmap_center_slice, check_worldmap_center
from .elevation import resolve_elevation
from .geo import Site
from .hydro_sources import resolve_hydro
from .hydro import HydroParams, build_water_layers, burn_channels, flowline_surface
from .normalize import plan_vertical, apply_vertical, to_uint16
from .terrain import fill_nodata, terracing_fraction, deterrace, slope_percent, buildable_fraction


@dataclass
class PipelineParams:
    exaggeration: float = 1.0
    sea_level_m: float | None = None       # in-game elevation of the reference water surface
    water_surface_real_m: float | None = None   # override the detected reference water surface (source datum metres)
    oversample: str | int = "auto"
    deterrace: str = "auto"                # auto | on | off
    burn: bool = True
    hydro: HydroParams = field(default_factory=HydroParams)
    floor_margin_m: float = 5.0
    water_sources: "WaterSourceParams | None" = None    # None -> defaults
    dem_source: str | None = None          # force an elevation source id (3dep, cop30, local:<name>)
    hydro_source: str | None = None        # force nhd or osm


@dataclass
class Result:
    site: Site
    playable_u16: np.ndarray
    world_u16: np.ndarray
    playable_m: np.ndarray                 # cs2 metres, float32
    world_m: np.ndarray
    playable_raw: np.ndarray               # real metres, before burning
    water_mask: np.ndarray                 # playable-res bool
    slope_pct: np.ndarray
    stats: dict
    placements: list = field(default_factory=list)   # guide.Placement records from producers (#12-#14)


def run(site: Site, p: PipelineParams, *, progress=print) -> Result:
    from .cache import SESSION, reset_session_stats
    reset_session_stats()
    t0 = time.time()
    timings: dict = {}
    _last = [t0]

    def lap(name: str) -> None:
        now = time.time(); timings[name] = round(now - _last[0], 1); _last[0] = now

    stats: dict = {"site": site.describe(), "params": {k: (asdict(v) if hasattr(v, "__dataclass_fields__") else v)
                                                       for k, v in asdict(p).items()}}

    # ---- 1. raw DEMs in metric CRS at exact pixel sizes --------------------
    # finest DTM covering each extent, else finest DSM (#16); playable and world may differ
    src_p = resolve_elevation(site, site.playable_bbox, prefer=p.dem_source, progress=progress)
    src_w = resolve_elevation(site, site.world_bbox, prefer=p.dem_source, progress=progress)
    play_raw, play_tf, meta_p = src_p.fetch(site, site.playable_bbox, spec.PLAYABLE_M_PER_PX, label="playable",
                                            oversample=p.oversample, progress=progress)
    world_raw, world_tf, meta_w = src_w.fetch(site, site.world_bbox, spec.WORLD_M_PER_PX, label="world", progress=progress)
    stats["dem_source"] = {
        # flat keys kept for the QA sheet / publish README
        "finest_ground_m": meta_p.native_m, "finest_name": meta_p.product, "datasets": meta_p.datasets,
        "playable_oversample": meta_p.oversample, "kind": meta_p.kind, "source": meta_p.source,
        "vertical_datum": meta_p.vertical_datum, "resample": meta_p.resample,
        "playable": meta_p.as_dict(), "world": meta_w.as_dict(),
        "mixed_sources": meta_p.source != meta_w.source,
    }
    if meta_p.kind == "dsm":
        progress(f"[elevation] WARNING: {meta_p.product} is a surface model (buildings/trees included); DSM cleaning is issue #18")
    from .fetch import DEM_SERVICE, NHD_SERVICE, NHD_LAYERS
    stats["sources"] = {
        "elevation": {"playable": meta_p.source, "world": meta_w.source,
                      "3dep": f"{DEM_SERVICE}/exportImage", "cop30": "https://copernicus-dem-30m.s3.amazonaws.com"},
        "hydrography": {"nhd": NHD_SERVICE, "nhd_layers": NHD_LAYERS, "osm": "Overpass API", "hydrorivers": "HydroSHEDS HydroRIVERS v1.0"},
    }
    play_raw, f1 = fill_nodata(play_raw)
    world_raw, f2 = fill_nodata(world_raw)
    stats["nodata_filled_fraction"] = {"playable": f1, "world": f2}
    assert play_raw.shape == (spec.HEIGHTMAP_SIZE,) * 2 and world_raw.shape == (spec.HEIGHTMAP_SIZE,) * 2
    lap("elevation")

    # ---- 2. de-terrace ------------------------------------------------------
    tf_play, tf_world = terracing_fraction(play_raw), terracing_fraction(world_raw)
    do_dt = {"on": True, "off": False}.get(p.deterrace, max(tf_play, tf_world) > 0.5)
    stats["terracing"] = {"playable_integer_fraction": tf_play, "world_integer_fraction": tf_world, "deterraced": do_dt}
    if do_dt:
        progress(f"[terrain] de-terracing (integer fraction playable={tf_play:.2f}, world={tf_world:.2f})")
        play_raw, world_raw = deterrace(play_raw), deterrace(world_raw)
    else:
        progress(f"[terrain] no terracing detected (integer fraction playable={tf_play:.2f}, world={tf_world:.2f})")

    # ---- 3. hydrography + channel burning ----------------------------------
    # NHD where 3DEP covers the site (US), OpenStreetMap + HydroRIVERS elsewhere (#17)
    hsrc = resolve_hydro(site, prefer=p.hydro_source, us=(src_p.id == "3dep"), progress=progress)
    flow, area, wb = hsrc.fetch(site, site.world_bbox, dem=(world_raw, world_tf), progress=progress)
    stats["hydro_source"] = {"id": hsrc.id, "flowlines": int(len(flow)), "areas": int(len(area)), "waterbodies": int(len(wb)),
                             "culverted": int(flow["culvert"].sum()) if "culvert" in flow else 0,
                             "order_sources": {k: int(v) for k, v in flow["order_source"].value_counts().items()} if "order_source" in flow else {}}

    lap("hydrography")
    layers_p = build_water_layers(play_raw, play_tf, spec.PLAYABLE_M_PER_PX, site.playable_bbox, flow, area, wb, p.hydro, progress=progress)
    layers_w = build_water_layers(world_raw, world_tf, spec.WORLD_M_PER_PX, site.world_bbox, flow, area, wb, p.hydro, progress=progress)
    stats["water_polygons_playable"] = layers_p.polygons
    stats["water_fraction"] = {"playable": float(layers_p.mask.mean()), "world": float(layers_w.mask.mean())}

    # reference water surface: the HIGHEST-ORDER water in the playable area, not the largest
    # pond. A polygon wins only if a flowline of the top order runs through it; otherwise the
    # p10 surface along the top-order flowlines; otherwise the playable minimum.
    from shapely.geometry import box as _box
    in_play = flow[flow.intersects(_box(*site.playable_bbox.as_tuple()))]
    if "culvert" in in_play.columns:
        in_play = in_play[~in_play["culvert"].fillna(False).astype(bool)]
    top_order = int(in_play["streamorde"].max()) if len(in_play) and in_play["streamorde"].notna().any() else 0
    polys_top = [q for q in layers_p.polygons if q["order"] >= top_order and top_order >= p.hydro.min_order]
    if p.water_surface_real_m is not None:
        water_ref, water_ref_desc = float(p.water_surface_real_m), "--water-surface override"
    elif polys_top:
        ref = polys_top[0]
        water_ref, water_ref_desc = ref["surface_p10_m"], f"{ref['name']} (order {ref['order']}, {ref['area_km2']} km2) p10 surface"
    elif top_order >= p.hydro.min_order and (fs := flowline_surface(play_raw, play_tf, spec.PLAYABLE_M_PER_PX, flow, site.playable_bbox, top_order)) is not None:
        names = sorted({str(n) for n in in_play.loc[in_play["streamorde"] == top_order, "gnis_name"].dropna()})[:3]
        water_ref, water_ref_desc = fs, f"p10 surface along order-{top_order} flowlines ({', '.join(names) or 'unnamed'})"
    elif layers_p.polygons:
        ref = layers_p.polygons[0]
        water_ref, water_ref_desc = ref["surface_p10_m"], f"{ref['name']} (largest water body, {ref['area_km2']} km2) p10 surface"
    else:
        water_ref, water_ref_desc = float(np.nanmin(play_raw)), "playable minimum (no water found)"
    progress(f"[hydro] reference water surface {water_ref:.2f} m = {water_ref_desc}")

    if p.burn:
        play_b, world_b = burn_channels(play_raw, layers_p), burn_channels(world_raw, layers_w)
        progress(f"[hydro] burned {layers_p.mask.mean():.2%} of playable, max carve {np.nanmax(play_raw - play_b):.1f} m")
    else:
        play_b, world_b = play_raw, world_raw

    lap("burn")

    # ---- 4. vertical: sea level, exaggeration, one height scale ------------
    v = plan_vertical(play_b, world_b, water_surface_real_m=water_ref, exaggeration=p.exaggeration,
                      sea_level_m=p.sea_level_m, floor_margin_m=p.floor_margin_m)
    play_m, world_m = apply_vertical(play_b, v), apply_vertical(world_b, v)
    stats["vertical"] = dict(asdict(v), water_surface_source=water_ref_desc)

    # ---- editor advice: water sources (#12) --------------------------------
    from .water_sources import propose_water_sources
    placements = propose_water_sources(flow, area, wb, site, play_tf, surface_real=layers_p.surface,
                                       dem_real=play_raw, water_mask=layers_p.mask, vertical=v,
                                       p=p.water_sources, progress=progress)
    stats["water_sources"] = [pl.record(i) for i, pl in enumerate(placements, 1)]

    # ---- 5. quantize, enforce world-centre contract, verify ----------------
    play_u16 = to_uint16(play_m, v.height_scale_m)
    world_u16 = to_uint16(world_m, v.height_scale_m)
    rs, cs = worldmap_center_slice()
    seam_before = float(np.abs(world_u16[rs, cs].astype(np.int64) - downsample_playable_to_world(play_u16).astype(np.int64)).mean() * v.m_per_level)
    world_u16[rs, cs] = downsample_playable_to_world(play_u16)
    check_worldmap_center(world_u16, play_u16)
    stats["world_center_mean_abs_diff_before_replace_m"] = seam_before

    # ---- stats -----------------------------------------------------------------
    # slope in in-game metres, so exaggeration changes buildability the way the player feels it
    slope = slope_percent(play_m, spec.PLAYABLE_M_PER_PX)
    build_land = buildable_fraction(slope, exclude=layers_p.mask)
    build_all = buildable_fraction(slope)
    pr = play_b[np.isfinite(play_b)]
    stats["playable"] = {
        "min_real_m": float(pr.min()), "max_real_m": float(pr.max()), "relief_m": float(pr.max() - pr.min()),
        "min_cs2_m": float(np.nanmin(play_m)), "max_cs2_m": float(np.nanmax(play_m)),
        "buildable_fraction_land": build_land, "buildable_fraction_all": build_all,
        "px_min": int(play_u16.min()), "px_max": int(play_u16.max()),
    }
    wr = world_b[np.isfinite(world_b)]
    stats["world"] = {"min_real_m": float(wr.min()), "max_real_m": float(wr.max()),
                      "px_min": int(world_u16.min()), "px_max": int(world_u16.max())}
    # slope histogram of land pixels (%): a smooth, empty-tailed histogram is the tell of upsampled data
    edges = [0, 2, 5, 10, 15, 20, 30, 45, 60, 90, 1e9]
    land = slope[~layers_p.mask]
    counts, _ = np.histogram(land, bins=edges)
    stats["slope_histogram"] = {"edges_pct": edges[:-1] + ["inf"], "fraction": [round(float(c) / max(land.size, 1), 4) for c in counts]}
    lap("finish")
    stats["timings_s"] = timings
    stats["downloaded"] = {"bytes": int(SESSION["bytes"]), "files": int(SESSION["files"])}
    stats["elapsed_s"] = round(time.time() - t0, 1)
    return Result(site, play_u16, world_u16, play_m, world_m, play_raw, layers_p.mask, slope, stats, placements)
