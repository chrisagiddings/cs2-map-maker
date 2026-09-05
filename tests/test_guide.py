import json

import numpy as np
import pytest

from src import spec
from src.guide import Placement, SYMBOLS, GROUP_ORDER, GuideError, sort_placements, write_placements, guide_sheet

N = spec.HEIGHTMAP_SIZE


def test_symbol_set_is_complete_and_distinct():
    assert set(s.group for s in SYMBOLS.values()) == set(GROUP_ORDER)
    # within a group, no two kinds share the same (marker, colour) pair
    for g in GROUP_ORDER:
        pairs = [(s.marker, s.color) for s in SYMBOLS.values() if s.group == g]
        assert len(pairs) == len(set(pairs)), g


def test_placement_coordinates():
    p = Placement("water.border_river_in", (0, N - 1))         # SW corner pixel
    assert p.xy_m == (0.0, 0.0) and p.xy_center_m == (-7168.0, -7168.0)
    q = Placement("connection.highway", (N - 1, 0))             # NE corner pixel
    assert q.xy_m == (14332.5, 14332.5)
    with pytest.raises(GuideError, match="unknown placement kind"):
        Placement("water.magic", (0, 0))
    with pytest.raises(GuideError, match="outside"):
        Placement("water.stream", (N, 0))


def test_sort_is_deterministic_by_group_then_position():
    ps = [Placement("utility.power", (10, 10)), Placement("water.stream", (500, 900)),
          Placement("water.stream", (100, 100)), Placement("connection.highway", (5, 5))]
    order = [(p.kind, p.px) for p in sort_placements(ps)]
    assert order == [("water.stream", (100, 100)), ("water.stream", (500, 900)),
                     ("connection.highway", (5, 5)), ("utility.power", (10, 10))]


def test_write_placements(tmp_path):
    ps = [Placement("water.border_river_in", (2811, 0), elev_m=63.0, label="Tennessee River inflow",
                    why="order-9 flowline enters at N edge", params={"flow": "in"}),
          Placement("trail.highway", (100, 100), geometry=[(100, 100), (2000, 2000)], label="I-24 to I-75")]
    j, m = write_placements(ps, tmp_path / "chatt", {"name": "chatt", "height_scale_m": 610, "sea_level_m": 63})
    d = json.loads(j.read_text(encoding="utf-8"))
    assert [r["number"] for r in d["placements"]] == [1, 2]
    r = d["placements"][0]
    assert r["kind"] == "water.border_river_in" and r["xy_m"] == [9838.5, 14332.5] and r["params"] == {"flow": "in"}
    md = m.read_text(encoding="utf-8")
    # 14332.5 formats as 14332 (round-half-even), matching the JSON value rounded the same way
    assert "| 1 | Border river, inflow | 9838, 14332 |" in md and "Suggested trunk highway" in md
    j2, m2 = write_placements([], tmp_path / "empty", {"name": "empty"})
    assert json.loads(j2.read_text(encoding="utf-8"))["placements"] == []
    assert "no placements yet" in m2.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def terrain():
    y, x = np.mgrid[0:N, 0:N]
    z = (60 + 200 * np.sin(x / 700) ** 2 + 0.01 * y).astype(np.float32)
    water = z < 63
    return z, water


def test_guide_sheet_renders_empty_and_populated(tmp_path, terrain):
    z, water = terrain
    p0 = guide_sheet(z, water, 63.0, "empty", [], tmp_path / "empty_guide.png", height_scale_m=610)
    assert p0.exists() and p0.stat().st_size > 50_000
    ps = [Placement(k, (200 + 150 * i, 300 + 200 * i), elev_m=63.0 + i, label=f"item {i}", why="because")
          for i, k in enumerate(k for k, s in SYMBOLS.items() if not s.line)]
    ps.append(Placement("trail.highway", (0, N // 2), geometry=[(0, N // 2), (N // 2, N // 3), (N - 1, N // 2)], label="trunk"))
    p1 = guide_sheet(z, water, 63.0, "full", ps, tmp_path / "full_guide.png", height_scale_m=610)
    assert p1.exists() and p1.stat().st_size > p0.stat().st_size * 0.9
    from PIL import Image
    im = Image.open(p1)
    assert im.size[0] == 2000 and im.mode == "RGBA" or im.mode == "RGB"
