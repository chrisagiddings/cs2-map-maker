"""Write and verify CS2 16-bit heightmap PNGs.

Writer: rasterio/GDAL (PNG driver, uint16, single band, no georeferencing).
Verifier: Pillow, deliberately a *different* library, so a silent 8-bit
downcast or channel expansion in the writer cannot verify itself.
"""
from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from PIL import Image

from . import spec


class ExportError(RuntimeError):
    """Raised when an array or a written file violates the CS2 contract."""


@dataclass(frozen=True)
class HeightmapStats:
    path: str
    shape: tuple[int, int]
    dtype: str
    min_px: int
    max_px: int
    unique_values: int

    def as_dict(self) -> dict:
        return asdict(self)


def _validate_array(arr: np.ndarray, size: int, dtype: str, what: str) -> None:
    if arr.ndim != 2:
        raise ExportError(f"{what}: expected a 2-D single-channel array, got ndim={arr.ndim}")
    if arr.shape != (size, size):
        raise ExportError(f"{what}: expected shape ({size}, {size}), got {arr.shape}")
    if arr.dtype != np.dtype(dtype):
        raise ExportError(f"{what}: expected dtype {dtype}, got {arr.dtype}")


def _write_png(path: Path, arr: np.ndarray, dtype: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(
            path, "w", driver="PNG",
            width=arr.shape[1], height=arr.shape[0], count=1, dtype=dtype,
        ) as ds:
            ds.write(arr, 1)
    # GDAL's PNG driver drops a .aux.xml sidecar next to the file; CS2 does not want it.
    aux = Path(str(path) + ".aux.xml")
    if aux.exists():
        aux.unlink()


def write_heightmap(path: str | os.PathLike, arr: np.ndarray, *, verify: bool = True) -> HeightmapStats:
    """Write a 4096x4096 uint16 array as a CS2 heightmap PNG and verify it.

    Raises ExportError if the array is wrong before writing, or if the
    independent read-back disagrees with what was written.
    """
    path = Path(path)
    _validate_array(arr, spec.HEIGHTMAP_SIZE, spec.HEIGHTMAP_DTYPE, "heightmap")
    _write_png(path, arr, spec.HEIGHTMAP_DTYPE)
    if not verify:
        return HeightmapStats(str(path), arr.shape, str(arr.dtype),
                              int(arr.min()), int(arr.max()), int(np.unique(arr).size))
    stats = verify_heightmap(path)
    back = np.array(Image.open(path))
    if not np.array_equal(back, arr):
        raise ExportError(f"{path}: read-back pixels differ from the array that was written")
    return stats


def verify_heightmap(path: str | os.PathLike, *, min_span: int = 1000) -> HeightmapStats:
    """Read a heightmap with Pillow and assert the CS2 contract.

    Checks shape (4096, 4096), dtype uint16, single channel, and that the
    pixel range actually spans the data (max - min >= min_span) so a file
    that got squashed to 0..255 or is constant fails loudly.
    """
    path = Path(path)
    if not path.exists():
        raise ExportError(f"{path}: file does not exist")
    with Image.open(path) as im:
        mode = im.mode
        arr = np.array(im)
    if arr.ndim != 2:
        raise ExportError(f"{path}: expected single-channel image, got mode={mode!r} shape={arr.shape}")
    if arr.shape != (spec.HEIGHTMAP_SIZE, spec.HEIGHTMAP_SIZE):
        raise ExportError(f"{path}: expected {spec.HEIGHTMAP_SIZE}x{spec.HEIGHTMAP_SIZE}, "
                          f"got {arr.shape[1]}x{arr.shape[0]}")
    if arr.dtype != np.uint16:
        raise ExportError(f"{path}: expected uint16 pixels, got {arr.dtype} (mode={mode!r}); silently downcast?")
    lo, hi = int(arr.min()), int(arr.max())
    if hi - lo < min_span:
        raise ExportError(f"{path}: pixel range {lo}..{hi} spans < {min_span}; data looks flat or squashed to 8-bit")
    return HeightmapStats(str(path), arr.shape, str(arr.dtype), lo, hi, int(np.unique(arr).size))


def downsample_playable_to_world(playable: np.ndarray) -> np.ndarray:
    """Block-mean the 4096 playable map by 4x to the 1024x1024 patch it occupies
    in the world map. Returns uint16 (rounded)."""
    r = spec.WORLD_TO_PLAYABLE_RATIO
    n = spec.HEIGHTMAP_SIZE
    blocks = playable.reshape(n // r, r, n // r, r).astype(np.float64)
    return np.rint(blocks.mean(axis=(1, 3))).astype(np.uint16)


def worldmap_center_slice() -> tuple[slice, slice]:
    """Row/col slices of the world map that the playable area occupies."""
    n, c = spec.HEIGHTMAP_SIZE, spec.WORLD_CENTER_PX
    off = (n - c) // 2
    return slice(off, off + c), slice(off, off + c)


def check_worldmap_center(world: np.ndarray, playable: np.ndarray, *, tolerance: int = 1) -> int:
    """Assert the world map centre 1024x1024 equals the playable map downsampled 4x.

    Returns the max absolute pixel difference. Raises ExportError above `tolerance`
    (1 LSB allowed for rounding).
    """
    _validate_array(world, spec.HEIGHTMAP_SIZE, spec.HEIGHTMAP_DTYPE, "worldmap")
    _validate_array(playable, spec.HEIGHTMAP_SIZE, spec.HEIGHTMAP_DTYPE, "heightmap")
    rs, cs = worldmap_center_slice()
    centre = world[rs, cs].astype(np.int64)
    expected = downsample_playable_to_world(playable).astype(np.int64)
    diff = int(np.abs(centre - expected).max())
    if diff > tolerance:
        raise ExportError(f"world map centre differs from downsampled playable map by up to "
                          f"{diff} px values (tolerance {tolerance})")
    return diff


def write_resource_mask(path: str | os.PathLike, mask: np.ndarray) -> None:
    """Write a 256x256 uint8 resource mask (255 = deposit present)."""
    path = Path(path)
    _validate_array(mask, spec.RESOURCE_MAP_SIZE, spec.RESOURCE_MAP_DTYPE, "resource mask")
    _write_png(path, mask, spec.RESOURCE_MAP_DTYPE)
    with Image.open(path) as im:
        back = np.array(im)
    if back.shape != mask.shape or back.dtype != np.uint8:
        raise ExportError(f"{path}: resource mask read-back is {back.dtype} {back.shape}")
