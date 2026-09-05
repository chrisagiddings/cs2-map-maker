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
from .fetch import fetch_dem, fetch_nhd, dem_source_info, FLOWLINE_FIELDS
from .geo import Site
from .hydro import HydroParams, build_water_layers, burn_channels
from .normalize import plan_vertical, apply_vertical, to_uint16
from .terrain import block_downsample, fill_nodata, terracing_fraction, deterrace, slope_percent, buildable_fraction


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
    t0 = time.time()
    stats: dict = {"site": site.describe(), "params": {k: (asdict(v) if hasattr(v, "__dataclass_fields__") else v)
                                                       for k, v in asdict(p).items()}}

    # ---- 1. raw DEMs in metric CRS at exact pixel sizes --------------------
    info = dem_source_info(site, site.playable_bbox)
    over = (2 if info.finest_ground_m < 2.5 else 1) if p.oversample == "auto" else int(p.oversample)
    stats["dem_source"] = {"finest_ground_m": info.finest_ground_m, "finest_name": info.finest_name,
                           "datasets": info.datasets, "playable_oversample": over}
    play_raw, play_tf, play_path = fetch_dem(site, site.playable_bbox, spec.PLAYABLE_M_PER_PX / over, label="playable", progress=progress)
    world_raw, world_tf, world_path = fetch_dem(site, site.world_bbox, spec.WORLD_M_PER_PX, label="world", progress=progress)
    from .fetch import DEM_SERVICE, NHD_SERVICE, NHD_LAYERS
    stats["sources"] = {
        "elevation": {"service": f"{DEM_SERVICE}/exportImage", "vertical_datum": "NAVD88 (3DEP)",
                      "playable_cache": str(play_path), "world_cache": str(world_path)},
        "hydrography": {"service": NHD_SERVICE, "layers": NHD_LAYERS},
    }
    play_raw, f1 = fill_nodata(play_raw)
    world_raw, f2 = fill_nodata(world_raw)
    stats["nodata_filled_fraction"] = {"playable": f1, "world": f2}
    if over > 1:
        play_raw = block_downsample(play_raw, over)
        from rasterio.transform import from_origin
        play_tf = from_origin(site.playable_bbox.minx, site.playable_bbox.maxy, spec.PLAYABLE_M_PER_PX, spec.PLAYABLE_M_PER_PX)
    assert play_raw.shape == (spec.HEIGHTMAP_SIZE,) * 2 and world_raw.shape == (spec.HEIGHTMAP_SIZE,) * 2

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
    flow = fetch_nhd(site, site.world_bbox, "flowline", out_fields=FLOWLINE_FIELDS, progress=progress)
    area = fetch_nhd(site, site.world_bbox, "area", out_fields="permanent_identifier,gnis_name,ftype,fcode,areasqkm", required=False, progress=progress)
    wb = fetch_nhd(site, site.world_bbox, "waterbody", out_fields="permanent_identifier,gnis_name,ftype,fcode,areasqkm", required=False, progress=progress)

    layers_p = build_water_layers(play_raw, play_tf, spec.PLAYABLE_M_PER_PX, site.playable_bbox, flow, area, wb, p.hydro, progress=progress)
    layers_w = build_water_layers(world_raw, world_tf, spec.WORLD_M_PER_PX, site.world_bbox, flow, area, wb, p.hydro, progress=progress)
    stats["water_polygons_playable"] = layers_p.polygons
    stats["water_fraction"] = {"playable": float(layers_p.mask.mean()), "world": float(layers_w.mask.mean())}

    # reference water surface: the largest water polygon in the playable area,
    # else the median DEM value along the highest-order flowline, else the playable minimum
    if p.water_surface_real_m is not None:
        water_ref, water_ref_desc = float(p.water_surface_real_m), "--water-surface override"
    elif layers_p.polygons:
        ref = layers_p.polygons[0]
        water_ref, water_ref_desc = ref["surface_p10_m"], f"{ref['name']} (order {ref['order']}, {ref['area_km2']} km2) p10 surface"
    elif layers_p.mask.any():
        water_ref = float(np.nanmedian(play_raw[layers_p.mask]))
        water_ref_desc = "median DEM along burned flowlines"
    else:
        water_ref, water_ref_desc = float(np.nanmin(play_raw)), "playable minimum (no water found)"
    progress(f"[hydro] reference water surface {water_ref:.2f} m = {water_ref_desc}")

    if p.burn:
        play_b, world_b = burn_channels(play_raw, layers_p), burn_channels(world_raw, layers_w)
        progress(f"[hydro] burned {layers_p.mask.mean():.2%} of playable, max carve {np.nanmax(play_raw - play_b):.1f} m")
    else:
        play_b, world_b = play_raw, world_raw

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
    stats["elapsed_s"] = round(time.time() - t0, 1)
    return Result(site, play_u16, world_u16, play_m, world_m, play_raw, layers_p.mask, slope, stats, placements)
