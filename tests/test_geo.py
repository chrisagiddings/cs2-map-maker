import numpy as np
import pytest

from src import spec
from src.geo import Site, BBox, utm_epsg_for, GeoError
from src.fetch import _tile_grid, FetchError
from src.cache import Cache, key_for


def test_utm_zones():
    assert utm_epsg_for(-85.3097, 35.0456) == 32616   # Chattanooga -> 16N
    assert utm_epsg_for(-122.42, 37.77) == 32610      # SF -> 10N
    assert utm_epsg_for(151.2, -33.9) == 32756        # Sydney -> 56S
    assert utm_epsg_for(5.7, 58.9) == 32632           # Norway exception
    with pytest.raises(GeoError):
        utm_epsg_for(0, 89)


def test_site_footprints_exact():
    s = Site.from_center("chatt", 35.0456, -85.3097)
    assert s.epsg == 32616
    p, w = s.playable_bbox, s.world_bbox
    assert p.width == spec.PLAYABLE_SIZE_M and p.height == spec.PLAYABLE_SIZE_M
    assert w.width == spec.WORLD_SIZE_M and w.height == spec.WORLD_SIZE_M
    # world is centred on playable
    assert np.isclose((p.minx + p.maxx) / 2, (w.minx + w.maxx) / 2)
    # round-trip centre back to lon/lat
    ll = s.bbox_wgs84(BBox(s.cx, s.cy, s.cx, s.cy))
    assert abs(ll.minx - -85.3097) < 1e-6 and abs(ll.miny - 35.0456) < 1e-6


def test_site_from_bbox_uses_centre():
    a = Site.from_bbox_wgs84("x", -85.4, 35.0, -85.2, 35.1)
    b = Site.from_center("x", 35.05, -85.3)
    assert a.epsg == b.epsg and abs(a.cx - b.cx) < 1e-6 and abs(a.cy - b.cy) < 1e-6


def test_tile_grid_covers_exactly():
    s = Site.from_center("chatt", 35.0456, -85.3097)
    w, h, tiles = _tile_grid(s.playable_bbox, 3.5, 2048)
    assert (w, h) == (4096, 4096) and len(tiles) == 4
    cover = np.zeros((h, w), bool)
    for r0, c0, tb, tw, th in tiles:
        cover[r0:r0 + th, c0:c0 + tw] = True
        assert abs(tb.width - tw * 3.5) < 1e-6 and abs(tb.height - th * 3.5) < 1e-6
    assert cover.all()
    # top-left tile's top edge is the bbox top edge (north-up rows)
    assert tiles[0][2].maxy == s.playable_bbox.maxy
    w, h, tiles = _tile_grid(s.world_bbox, 14.0, 2048)
    assert (w, h) == (4096, 4096)
    w, h, tiles = _tile_grid(s.playable_bbox, 1.75, 2048)
    assert (w, h) == (8192, 8192) and len(tiles) == 16
    with pytest.raises(FetchError):
        _tile_grid(s.playable_bbox, 3.0, 2048)


def test_cache_roundtrip(tmp_path):
    c = Cache(tmp_path)
    k = key_for("x", {"a": 1, "b": [1, 2]})
    assert k == key_for("x", {"b": [1, 2], "a": 1})     # order-insensitive
    assert c.get("src", k, "bin") is None
    p = c.put("src", k, "bin", b"hello", {"url": "u"})
    assert c.get("src", k, "bin") == p and p.read_bytes() == b"hello"
    assert (tmp_path / "src" / f"{k}.json").exists()
