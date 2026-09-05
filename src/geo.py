"""Site geometry: centre -> local UTM CRS -> exact CS2 footprints.

The CS2 footprint is fixed (14.336 km playable, 57.344 km world), so a site is
fully described by its centre point. `--bbox` input is honoured by taking its
centre; the footprint itself never changes size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

from pyproj import CRS, Transformer

from . import spec


class GeoError(ValueError):
    pass


@dataclass(frozen=True)
class BBox:
    minx: float
    miny: float
    maxx: float
    maxy: float

    @property
    def width(self) -> float:
        return self.maxx - self.minx

    @property
    def height(self) -> float:
        return self.maxy - self.miny

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.minx, self.miny, self.maxx, self.maxy)

    def as_str(self, ndigits: int = 3) -> str:
        return ",".join(f"{v:.{ndigits}f}" for v in self.as_tuple())


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the WGS84 UTM zone containing (lon, lat)."""
    if not (-180 <= lon <= 180 and -80 <= lat <= 84):
        raise GeoError(f"lon/lat {lon},{lat} outside UTM coverage")
    zone = int(math.floor((lon + 180) / 6)) + 1
    # Norway / Svalbard exceptions
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32
    if 72 <= lat < 84:
        if 0 <= lon < 9: zone = 31
        elif 9 <= lon < 21: zone = 33
        elif 21 <= lon < 33: zone = 35
        elif 33 <= lon < 42: zone = 37
    return (32600 if lat >= 0 else 32700) + zone


@dataclass(frozen=True)
class Site:
    name: str
    lat: float
    lon: float
    epsg: int              # metric working CRS (UTM)
    cx: float              # centre easting
    cy: float              # centre northing

    @classmethod
    def from_center(cls, name: str, lat: float, lon: float, epsg: int | None = None) -> "Site":
        epsg = epsg or utm_epsg_for(lon, lat)
        tf = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        cx, cy = tf.transform(lon, lat)
        return cls(name, lat, lon, epsg, float(cx), float(cy))

    @classmethod
    def from_bbox_wgs84(cls, name: str, minx: float, miny: float, maxx: float, maxy: float,
                        epsg: int | None = None) -> "Site":
        if not (minx < maxx and miny < maxy):
            raise GeoError(f"bbox must be minx,miny,maxx,maxy in degrees; got {minx},{miny},{maxx},{maxy}")
        return cls.from_center(name, (miny + maxy) / 2, (minx + maxx) / 2, epsg)

    @property
    def crs(self) -> CRS:
        return CRS.from_epsg(self.epsg)

    def _square(self, side_m: float) -> BBox:
        h = side_m / 2
        return BBox(self.cx - h, self.cy - h, self.cx + h, self.cy + h)

    @property
    def playable_bbox(self) -> BBox:
        return self._square(spec.PLAYABLE_SIZE_M)

    @property
    def world_bbox(self) -> BBox:
        return self._square(spec.WORLD_SIZE_M)

    def bbox_wgs84(self, bbox: BBox) -> BBox:
        """Loose lon/lat envelope of a UTM bbox (for services that want degrees)."""
        tf = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        xs = [bbox.minx, bbox.maxx, bbox.maxx, bbox.minx]
        ys = [bbox.miny, bbox.miny, bbox.maxy, bbox.maxy]
        lons, lats = tf.transform(xs, ys)
        return BBox(min(lons), min(lats), max(lons), max(lats))

    def describe(self) -> dict:
        d = asdict(self)
        d["playable_bbox_utm"] = self.playable_bbox.as_tuple()
        d["world_bbox_utm"] = self.world_bbox.as_tuple()
        d["playable_bbox_wgs84"] = self.bbox_wgs84(self.playable_bbox).as_tuple()
        d["world_bbox_wgs84"] = self.bbox_wgs84(self.world_bbox).as_tuple()
        return d
