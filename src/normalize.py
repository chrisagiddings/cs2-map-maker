"""Vertical decisions: sea level, exaggeration, one height scale for both maps."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import spec


class NormalizeError(ValueError):
    pass


@dataclass(frozen=True)
class Vertical:
    water_surface_real_m: float     # elevation of the reference water surface in the source datum
    sea_level_m: float              # where that surface sits in CS2 metres (above pixel 0)
    offset_m: float                 # cs2 = (real - water_surface_real) * exag + sea_level
    exaggeration: float
    height_scale_m: float           # type this into the editor
    union_min_m: float              # after transform
    union_max_m: float
    m_per_level: float              # vertical resolution of one uint16 step


def apply_vertical(real: np.ndarray, v: Vertical) -> np.ndarray:
    return ((real - v.water_surface_real_m) * v.exaggeration + v.sea_level_m).astype(np.float32)


def plan_vertical(playable: np.ndarray, world: np.ndarray, *, water_surface_real_m: float,
                  exaggeration: float = 1.0, sea_level_m: float | None = None,
                  floor_margin_m: float = 5.0, headroom: float = 1.02,
                  round_to_m: float = 10.0) -> Vertical:
    """Decide the vertical transform shared by both maps.

    sea_level_m: requested in-game elevation of the reference water surface.
      None -> the lowest value it can take while keeping every pixel of both
      maps >= floor_margin_m above 0 (nothing gets clipped to pixel 0).
    The height scale is the smallest round number >= union max * headroom.
    """
    lo = float(min(np.nanmin(playable), np.nanmin(world)))
    hi = float(max(np.nanmax(playable), np.nanmax(world)))
    # lowest feasible sea level: the union minimum must map to >= floor_margin
    min_feasible = (water_surface_real_m - lo) * exaggeration + floor_margin_m
    if sea_level_m is None:
        sea_level_m = math.ceil(min_feasible)
    elif sea_level_m < min_feasible:
        raise NormalizeError(
            f"--sea-level {sea_level_m} m would push the lowest terrain ({lo:.1f} m real, "
            f"{(lo - water_surface_real_m) * exaggeration:+.1f} m relative to the water surface) below "
            f"pixel 0. Minimum feasible is {min_feasible:.1f} m.")
    umin = (lo - water_surface_real_m) * exaggeration + sea_level_m
    umax = (hi - water_surface_real_m) * exaggeration + sea_level_m
    hs = math.ceil(umax * headroom / round_to_m) * round_to_m
    if hs > spec.HEIGHT_SCALE_MAX_M:
        raise NormalizeError(f"height scale {hs} m exceeds CS2 maximum {spec.HEIGHT_SCALE_MAX_M} m; "
                             f"lower --exaggeration or --sea-level")
    hs = max(hs, spec.HEIGHT_SCALE_MIN_M)
    return Vertical(water_surface_real_m, float(sea_level_m), float(sea_level_m - water_surface_real_m * exaggeration),
                    exaggeration, float(hs), umin, umax, hs / spec.HEIGHTMAP_MAX)


def to_uint16(cs2_m: np.ndarray, height_scale_m: float) -> np.ndarray:
    scaled = cs2_m / height_scale_m * spec.HEIGHTMAP_MAX
    if np.nanmin(scaled) < -0.5 or np.nanmax(scaled) > spec.HEIGHTMAP_MAX + 0.5:
        raise NormalizeError(f"values {np.nanmin(cs2_m):.1f}..{np.nanmax(cs2_m):.1f} m do not fit height scale {height_scale_m} m")
    return np.clip(np.rint(scaled), 0, spec.HEIGHTMAP_MAX).astype(np.uint16)
