"""Raster terrain operations: resampling, nodata fill, de-terracing, slope."""
from __future__ import annotations

import numpy as np
from scipy import ndimage


class TerrainError(RuntimeError):
    pass


def block_downsample(a: np.ndarray, factor: int) -> np.ndarray:
    """Mean over factor x factor blocks (NaN-aware). Shape must divide evenly."""
    if factor == 1:
        return a.astype(np.float32, copy=False)
    h, w = a.shape
    if h % factor or w % factor:
        raise TerrainError(f"shape {a.shape} not divisible by {factor}")
    b = a.reshape(h // factor, factor, w // factor, factor)
    with np.errstate(invalid="ignore"):
        out = np.nanmean(b, axis=(1, 3))
    return out.astype(np.float32)


def fill_nodata(a: np.ndarray, max_iter: int = 200) -> tuple[np.ndarray, float]:
    """Fill NaN holes by nearest-valid value. Returns (filled, fraction_filled)."""
    nan = ~np.isfinite(a)
    frac = float(nan.mean())
    if frac == 0.0:
        return a, 0.0
    if frac == 1.0:
        raise TerrainError("DEM is entirely nodata")
    idx = ndimage.distance_transform_edt(nan, return_distances=False, return_indices=True)
    return a[tuple(idx)], frac


def terracing_fraction(a: np.ndarray, step_m: float = 1.0, tol: float = 0.01) -> float:
    """Fraction of pixels whose value sits on a `step_m` grid. ~1.0 means an
    integer-metre source DEM (terraced); LiDAR floats come out near 0."""
    v = a[np.isfinite(a)]
    v = v[v != 0.0]                      # exact zeros are sea / fill, not contour steps
    if v.size == 0:
        return 0.0
    if v.size > 2_000_000:
        v = v[:: v.size // 2_000_000]
    r = np.abs(v / step_m - np.round(v / step_m))
    return float((r < tol).mean())


def deterrace(a: np.ndarray, *, flat_range_m: float = 2.5, size: int = 7) -> np.ndarray:
    """Edge-preserving smoothing for terraced DEMs.

    Smooths only where the local range (max-min in a size x size window) is
    small, i.e. gentle ground where 1 m contour steps are a few pixels apart.
    A 7 px window at 3.5 m/px is 24.5 m; a 2.5 m range over that is ~10% grade,
    so anything steeper (ridgelines, cliffs, bluffs) is left untouched. This is
    a cheap stand-in for a bilateral filter that behaves the same on step edges.
    """
    lo = ndimage.minimum_filter(a, size=size)
    hi = ndimage.maximum_filter(a, size=size)
    flat = (hi - lo) <= flat_range_m
    out = np.where(flat, ndimage.uniform_filter(a, size=size), a)
    # second pass on the masked result, so non-flat pixels (cliffs) never bleed in
    out = np.where(flat, ndimage.uniform_filter(out, size=size), out)
    return out.astype(np.float32)


def slope_percent(a: np.ndarray, m_per_px: float) -> np.ndarray:
    gy, gx = np.gradient(a.astype(np.float64), m_per_px)
    return (np.hypot(gx, gy) * 100.0).astype(np.float32)


def buildable_fraction(slope_pct: np.ndarray, exclude: np.ndarray | None = None, threshold: float = 10.0) -> float:
    m = slope_pct < threshold
    if exclude is not None:
        keep = ~exclude
        if keep.sum() == 0:
            return 0.0
        return float(m[keep].mean())
    return float(m.mean())
