"""Render a guide sheet with one sample of every symbol on a built map's terrain,
to review the symbol set and layout before real producers exist (#11).

    python tools/demo_guide.py --name chattanooga
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import spec                                        # noqa: E402
from src.guide import Placement, SYMBOLS, guide_sheet       # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    d = Path(a.out) / a.name
    from PIL import Image
    man = json.loads((d / f"{a.name}_manifest.json").read_text(encoding="utf-8"))
    hs, sea = man["vertical"]["height_scale_m"], man["vertical"]["sea_level_m"]
    z = np.array(Image.open(d / f"{a.name}_heightmap.png")).astype(np.float32) / 65535 * hs
    water = z <= sea + 0.2
    n = spec.HEIGHTMAP_SIZE
    ps = []
    kinds = [k for k, s in SYMBOLS.items() if not s.line]
    rng = np.random.default_rng(1)
    for i, k in enumerate(kinds):
        c, r = int(rng.integers(300, n - 300)), int(rng.integers(300, n - 300))
        if k.startswith("water.border") or k.startswith("connection") or k.startswith("utility"):
            # edge kinds sit on the border
            side = i % 4
            c, r = [(c, 0), (n - 1, r), (c, n - 1), (0, r)][side]
        ps.append(Placement(k, (c, r), elev_m=float(z[r, c]), label=f"sample {SYMBOLS[k].label.lower()}",
                            why="demo placement, not a recommendation"))
    ps.append(Placement("trail.highway", (0, n // 2), geometry=[(0, n // 2), (n // 3, n // 2 - 400), (2 * n // 3, n // 2 + 300), (n - 1, n // 2)],
                        label="sample trunk", why="demo route"))
    ps.append(Placement("trail.rail", (0, n // 3), geometry=[(0, n // 3), (n - 1, 2 * n // 3)], label="sample rail", why="demo route"))
    out = guide_sheet(z, water, sea, f"{a.name} (symbol demo)", ps, d / f"{a.name}_guide_demo.png", height_scale_m=hs)
    print("demo ->", out)


if __name__ == "__main__":
    main()
