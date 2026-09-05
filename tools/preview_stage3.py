"""Quick Stage 3 look: hillshade of the burned playable map, raw-vs-burned
difference, and a cross-section through the middle. The full QA contact sheet
comes in Stage 4; this only exists to eyeball the channel burning.

    python tools/preview_stage3.py --name chattanooga
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import spec                      # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    d = Path(a.out) / a.name
    from PIL import Image
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource

    man = json.loads((d / f"{a.name}_manifest.json").read_text())
    hs = man["vertical"]["height_scale_m"]
    hm = np.array(Image.open(d / f"{a.name}_heightmap.png")).astype(np.float32) / 65535 * hs
    wm = np.array(Image.open(d / f"{a.name}_worldmap.png")).astype(np.float32) / 65535 * hs
    sea = man["vertical"]["sea_level_m"]

    fig, ax = plt.subplots(2, 2, figsize=(16, 15), dpi=100)
    ls = LightSource(315, 45)
    s = 4
    hm_d, wm_d = hm[::s, ::s], wm[::s, ::s]
    ax[0, 0].imshow(ls.shade(hm_d, cmap=plt.cm.gray, vert_exag=2, dx=3.5 * s, dy=3.5 * s, blend_mode="overlay"))
    ax[0, 0].set_title(f"playable hillshade (burned)  height scale {hs:.0f} m")
    ax[0, 1].imshow(ls.shade(wm_d, cmap=plt.cm.gray, vert_exag=2, dx=14 * s, dy=14 * s, blend_mode="overlay"))
    r0 = 1536 // s; r1 = 2560 // s
    ax[0, 1].plot([r0, r1, r1, r0, r0], [r0, r0, r1, r1, r0], "r-", lw=1)
    ax[0, 1].set_title("world hillshade with playable footprint")
    # water: everything at/below the sea level + 0.2 m in the playable
    im = ax[1, 0].imshow(np.where(hm_d <= sea + 0.2, 1, 0), cmap="Blues", vmin=0, vmax=1.3)
    ax[1, 0].set_title(f"pixels at or below the in-game water surface ({sea:.0f} m)")
    mid = hm.shape[0] // 2
    xs = np.arange(hm.shape[1]) * spec.PLAYABLE_M_PER_PX / 1000
    ax[1, 1].plot(xs, hm[mid, :], lw=0.6, label="E-W through centre")
    ax[1, 1].plot(xs, hm[:, mid], lw=0.6, label="N-S through centre")
    ax[1, 1].axhline(sea, color="deepskyblue", lw=0.8, label=f"water surface {sea:.0f} m")
    ax[1, 1].set_xlabel("km"); ax[1, 1].set_ylabel("in-game m"); ax[1, 1].legend(); ax[1, 1].grid(alpha=0.3)
    ax[1, 1].set_title("cross-sections (in-game metres)")
    for x in ax.flat[:3]:
        x.set_xticks([]); x.set_yticks([])
    fig.tight_layout()
    out = d / f"{a.name}_stage3_preview.png"
    fig.savefig(out)
    print("preview ->", out)


if __name__ == "__main__":
    main()
