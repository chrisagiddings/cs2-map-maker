"""QA contact sheet: the one image to look at after every run.

Panels: playable hillshade (water tinted), world hillshade with the playable
footprint outlined, slope/buildability classes, two cross-sections through the
centre, and a text panel with the numbers that matter (height scale first).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import spec

INK = "#1f2328"
MUTED = "#6b7280"
GRID = "#d0d4da"
WATER = "#3b82c4"
PROFILE_EW = "#1d4ed8"     # blue
PROFILE_NS = "#d97706"     # amber
# one-hue sequential for slope class (light = flat, dark = steep)
SLOPE_RAMP = ["#fde8d0", "#f8b988", "#e5773a", "#a33d0c"]
SLOPE_EDGES = [0, 5, 10, 20, 1e9]
SLOPE_LABELS = ["< 5 % flat", "5–10 % buildable", "10–20 % marginal", "> 20 % steep"]


def _shade(z: np.ndarray, m_per_px: float, vert_exag: float = 2.0):
    from matplotlib.colors import LightSource
    ls = LightSource(azdeg=315, altdeg=45)
    return ls.hillshade(z.astype(np.float64), vert_exag=vert_exag, dx=m_per_px, dy=m_per_px)


def _block(a: np.ndarray, f: int):
    h, w = a.shape
    return a[: h - h % f, : w - w % f].reshape(h // f, f, w // f, f).mean(axis=(1, 3))


def contact_sheet(res, path: str | Path, *, max_px: int = 2000) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch, Rectangle
    from matplotlib.lines import Line2D

    st = res.stats
    v = st["vertical"]
    pl = st["playable"]
    hs = v["height_scale_m"]
    sea = v["sea_level_m"]
    name = res.site.name

    f = 4                                              # 4096 -> 1024 for display
    play_m = _block(res.playable_m, f)
    world_m = _block(res.world_m, f)
    water = _block(res.water_mask.astype(np.float32), f) > 0.5
    slope = _block(res.slope_pct, f)

    dpi = 100
    fig = plt.figure(figsize=(max_px / dpi, max_px * 0.60 / dpi), dpi=dpi, facecolor="white")
    gs = fig.add_gridspec(2, 3, height_ratios=[1.0, 0.62], hspace=0.14, wspace=0.06,
                          left=0.035, right=0.98, top=0.92, bottom=0.07)
    ax_p, ax_w, ax_s = (fig.add_subplot(gs[0, i]) for i in range(3))
    ax_x = fig.add_subplot(gs[1, :2])
    ax_t = fig.add_subplot(gs[1, 2])

    # --- playable hillshade, water tinted --------------------------------------
    sh = _shade(play_m, spec.PLAYABLE_M_PER_PX * f)
    rgb = np.repeat(sh[..., None], 3, axis=2)
    tint = np.array([0.23, 0.51, 0.77])
    rgb[water] = rgb[water] * 0.35 + tint * 0.65
    ax_p.imshow(rgb, interpolation="bilinear")
    ax_p.set_title(f"playable 14.336 km  ·  hillshade, water tinted", loc="left", color=INK, fontsize=11)

    # --- world hillshade with footprint ---------------------------------------
    shw = _shade(world_m, spec.WORLD_M_PER_PX * f)
    ax_w.imshow(shw, cmap="gray", vmin=0, vmax=1, interpolation="bilinear")
    off, size = (spec.HEIGHTMAP_SIZE - spec.WORLD_CENTER_PX) // 2 // f, spec.WORLD_CENTER_PX // f
    ax_w.add_patch(Rectangle((off, off), size, size, fill=False, edgecolor="#e11d48", lw=1.5))
    ax_w.set_title("world 57.344 km  ·  playable footprint in red", loc="left", color=INK, fontsize=11)

    # --- slope / buildability classes -----------------------------------------
    cls = np.digitize(slope, SLOPE_EDGES[1:-1])       # 0..3
    from matplotlib.colors import ListedColormap, to_rgb
    img = np.array([to_rgb(c) for c in SLOPE_RAMP])[cls]
    img[water] = to_rgb(WATER)
    ax_s.imshow(img, interpolation="nearest")
    ax_s.set_title(f"slope classes  ·  {pl['buildable_fraction_land']:.0%} of land under 10 %", loc="left", color=INK, fontsize=11)
    handles = [Patch(facecolor=c, edgecolor="none", label=l) for c, l in zip(SLOPE_RAMP, SLOPE_LABELS)]
    handles.append(Patch(facecolor=WATER, edgecolor="none", label="water"))
    ax_s.legend(handles=handles, loc="lower left", fontsize=8, frameon=True, framealpha=0.9, edgecolor=GRID)

    for ax in (ax_p, ax_w, ax_s):
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_edgecolor(GRID)

    # --- cross-sections ---------------------------------------------------------
    n = res.playable_m.shape[0]
    mid = n // 2
    km = np.arange(n) * spec.PLAYABLE_M_PER_PX / 1000
    ew, ns = res.playable_m[mid, :], res.playable_m[:, mid]
    ax_x.fill_between(km, 0, ew, color=PROFILE_EW, alpha=0.08, lw=0)
    ax_x.plot(km, ew, color=PROFILE_EW, lw=1.2, label="W→E through centre (row 2048)")
    ax_x.plot(km, ns, color=PROFILE_NS, lw=1.2, label="N→S through centre (col 2048)")
    ax_x.axhline(sea, color=WATER, lw=1.0, ls="--", label=f"water surface {sea:.0f} m")
    ax_x.set_xlim(0, km[-1]); ax_x.set_ylim(0, max(hs * 0.0 + np.nanmax(res.playable_m) * 1.08, sea + 20))
    ax_x.set_xlabel("km across playable area", color=MUTED, fontsize=9)
    ax_x.set_ylabel("in-game metres", color=MUTED, fontsize=9)
    ax_x.grid(color=GRID, lw=0.6, alpha=0.7); ax_x.tick_params(colors=MUTED, labelsize=8)
    for s in ax_x.spines.values():
        s.set_edgecolor(GRID)
    ax_x.legend(loc="upper right", fontsize=8, frameon=False)
    ax_x.set_title("cross-sections through the centre  ·  in-game metres, not to scale", loc="left", color=INK, fontsize=11)
    # draw the section lines on the playable hillshade so the reader can locate them
    ax_p.axhline(mid / f, color=PROFILE_EW, lw=0.8, alpha=0.8)
    ax_p.axvline(mid / f, color=PROFILE_NS, lw=0.8, alpha=0.8)

    # --- text panel ---------------------------------------------------------------
    ax_t.set_axis_off()
    src = st["dem_source"]
    hyd = st.get("water_polygons_playable") or []
    ref = hyd[0] if hyd else None
    lines = [
        ("HEIGHT SCALE (type into editor)", f"{hs:.0f} m", True),
        ("water surface, in-game", f"{sea:.1f} m", False),
        ("water surface, real (NAVD88)", f"{v['water_surface_real_m']:.1f} m", False),
        ("reference water", (f"{ref['name']} · order {ref['order']} · {ref['area_km2']:.1f} km²" if ref else "none"), False),
        ("playable elevation, real", f"{pl['min_real_m']:.0f} – {pl['max_real_m']:.0f} m  (relief {pl['relief_m']:.0f} m)", False),
        ("playable elevation, in-game", f"{pl['min_cs2_m']:.0f} – {pl['max_cs2_m']:.0f} m", False),
        ("world elevation, in-game", f"{v['union_min_m']:.0f} – {v['union_max_m']:.0f} m", False),
        ("vertical exaggeration", f"×{v['exaggeration']:.2f}", False),
        ("buildable < 10 % slope", f"{pl['buildable_fraction_land']:.1%} of land  ·  {pl['buildable_fraction_all']:.1%} of all", False),
        ("water coverage", f"{st['water_fraction']['playable']:.1%} of playable", False),
        ("channel burning", "on" if st["params"]["burn"] else "OFF", False),
        ("source DEM", f"~{src['finest_ground_m']} m  ({src['finest_name']})", False),
        ("playable fetched at", f"{spec.PLAYABLE_M_PER_PX / src['playable_oversample']:.2f} m/px, {src['playable_oversample']}× → 3.5 m", False),
        ("terracing", ("de-terraced" if st["terracing"]["deterraced"] else f"none ({st['terracing']['playable_integer_fraction']:.0%} integer px)"), False),
        ("pixel range", f"hm {pl['px_min']}–{pl['px_max']}  ·  wm {st['world']['px_min']}–{st['world']['px_max']}  ·  1 level = {v['m_per_level'] * 100:.1f} cm", False),
        ("CRS", f"EPSG:{res.site.epsg}   centre {res.site.lat:.4f}, {res.site.lon:.4f}", False),
    ]
    y = 0.98
    for label, value, big in lines:
        if big:
            ax_t.text(0.0, y, label, transform=ax_t.transAxes, fontsize=9, color=MUTED, va="top")
            ax_t.text(0.0, y - 0.05, value, transform=ax_t.transAxes, fontsize=26, color=INK, weight="bold", va="top")
            y -= 0.17
        else:
            ax_t.text(0.0, y, label, transform=ax_t.transAxes, fontsize=8.2, color=MUTED, va="top")
            ax_t.text(1.0, y, value, transform=ax_t.transAxes, fontsize=8.6, color=INK, va="top", ha="right")
            y -= 0.055

    fig.suptitle(f"{name}  —  CS2 heightmap QA", x=0.02, ha="left", fontsize=15, color=INK, weight="bold")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return path
