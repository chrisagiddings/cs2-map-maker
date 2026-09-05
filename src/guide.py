"""Placement guide sheet: the canvas, symbol set, legend and placement record
that the editor-advice producers (water sources, resources, connections) draw
onto. This module knows nothing about *what* to place; producers append
`Placement` records and `guide_sheet()` renders them all on one image with a
numbered callout table. See issue #9 / #11.

Coordinates: `px` is (col, row) in the 4096² playable heightmap. `xy_m` is
metres from the south-west corner of the playable area (x east, y north).
`xy_center_m` is the same point relative to the map centre, which is what the
CS2 editor shows for a selected object.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

from . import spec

# --- symbol set --------------------------------------------------------------
# Okabe-Ito palette (colour-blind safe). Every kind has a distinct marker shape
# as well, so identity never rests on colour alone.
BLUE, SKY, GREEN, YELLOW, ORANGE, VERMILION, PURPLE, BLACK, GREY = (
    "#0072B2", "#56B4E9", "#009E73", "#F0E442", "#E69F00", "#D55E00", "#CC79A7", "#000000", "#7F7F7F")


@dataclass(frozen=True)
class Symbol:
    marker: str
    color: str
    label: str
    group: str
    line: bool = False          # drawn as a polyline (geometry) instead of a point


SYMBOLS: dict[str, Symbol] = {
    "water.border_river_in":  Symbol("^", BLUE,   "Border river, inflow",      "Water sources"),
    "water.border_river_out": Symbol("v", BLUE,   "Border river, outflow",     "Water sources"),
    "water.border_sea":       Symbol("s", SKY,    "Border sea",                "Water sources"),
    "water.stream":           Symbol("o", SKY,    "Stream source",             "Water sources"),
    "water.lake":             Symbol("D", BLUE,   "Constant-level lake",       "Water sources"),
    "resource.ore":           Symbol("p", PURPLE, "Ore deposit",               "Resources"),
    "resource.oil":           Symbol("H", BLACK,  "Oil deposit",               "Resources"),
    "resource.fertile":       Symbol("P", GREEN,  "Fertile land",              "Resources"),
    "resource.water":         Symbol("h", SKY,    "Ground water",              "Resources"),
    "connection.highway":     Symbol("*", VERMILION, "Highway connection",     "Outside connections"),
    "connection.rail":        Symbol("X", ORANGE, "Rail connection",           "Outside connections"),
    "connection.ship":        Symbol("8", BLUE,   "Ship connection",           "Outside connections"),
    "connection.air":         Symbol(">", YELLOW, "Air connection",            "Outside connections"),
    "utility.power":          Symbol("x", YELLOW, "Power connection",          "Utilities"),
    "utility.water":          Symbol("d", BLUE,   "Water connection",          "Utilities"),
    "utility.sewer":          Symbol("d", GREY,   "Sewer outflow",             "Utilities"),
    "trail.highway":          Symbol("--", VERMILION, "Suggested trunk highway", "Routes", line=True),
    "trail.rail":             Symbol("--", ORANGE, "Suggested rail trunk",     "Routes", line=True),
}
GROUP_ORDER = ["Water sources", "Resources", "Outside connections", "Utilities", "Routes"]
UNFILLED_MARKERS = set("x+|_1234")


def _marker_kw(sym: "Symbol", ms: float) -> dict:
    """Matplotlib marker kwargs. Unfilled markers (x, +) have no face, so they get
    the colour on the edge instead of a white halo, or they vanish."""
    if sym.marker in UNFILLED_MARKERS:
        return dict(marker=sym.marker, ms=ms, mec=sym.color, mew=3.0, ls="none")
    if sym.marker == "*":
        ms *= 1.45                 # the star glyph is visually smaller than the others at equal size
    return dict(marker=sym.marker, ms=ms, mfc=sym.color, mec="white", mew=1.4, ls="none")


class GuideError(ValueError):
    pass


# --- placement record ----------------------------------------------------------
@dataclass
class Placement:
    kind: str
    px: tuple[int, int]                       # (col, row) in the playable heightmap
    elev_m: float | None = None               # in-game metres
    label: str = ""
    why: str = ""
    params: dict = field(default_factory=dict)
    geometry: list[tuple[int, int]] | None = None   # polyline in px for line kinds

    def __post_init__(self):
        if self.kind not in SYMBOLS:
            raise GuideError(f"unknown placement kind {self.kind!r}; known: {sorted(SYMBOLS)}")
        c, r = self.px
        n = spec.HEIGHTMAP_SIZE
        if not (0 <= c < n and 0 <= r < n):
            raise GuideError(f"{self.kind} px {self.px} outside the {n}x{n} playable map")

    @property
    def xy_m(self) -> tuple[float, float]:
        c, r = self.px
        m = spec.PLAYABLE_M_PER_PX
        return (round(c * m, 1), round((spec.HEIGHTMAP_SIZE - 1 - r) * m, 1))

    @property
    def xy_center_m(self) -> tuple[float, float]:
        x, y = self.xy_m
        h = spec.PLAYABLE_SIZE_M / 2
        return (round(x - h, 1), round(y - h, 1))

    def record(self, number: int) -> dict:
        d = asdict(self)
        d.update(number=number, xy_m=self.xy_m, xy_center_m=self.xy_center_m, symbol=SYMBOLS[self.kind].label)
        return d


def sort_placements(placements: list[Placement]) -> list[Placement]:
    """Deterministic order: by group, then kind, then north-to-south, west-to-east."""
    gi = {g: i for i, g in enumerate(GROUP_ORDER)}
    return sorted(placements, key=lambda p: (gi[SYMBOLS[p.kind].group], p.kind, p.px[1], p.px[0]))


# --- outputs -------------------------------------------------------------------
def write_placements(placements: list[Placement], stem: Path, meta: dict) -> tuple[Path, Path]:
    ps = sort_placements(placements)
    recs = [p.record(i) for i, p in enumerate(ps, 1)]
    jpath = stem.with_name(stem.name + "_placements.json")
    mpath = stem.with_name(stem.name + "_placements.md")
    jpath.write_text(json.dumps({"meta": meta, "coordinate_note": "xy_m from the SW corner of the playable area, "
                                 "x east / y north; xy_center_m relative to the map centre", "placements": recs},
                                indent=1, default=str), encoding="utf-8")
    lines = [f"# {meta.get('name', 'map')} placement guide", "",
             f"Height scale {meta.get('height_scale_m', '?')} m · water surface {meta.get('sea_level_m', '?')} m in-game · "
             f"coordinates in metres from the SW corner (x east, y north), centre-relative in brackets", "",
             "| # | What | x, y (m) | centre-relative | elev (m) | Label | Why |", "|---|---|---|---|---|---|---|"]
    for r in recs:
        x, y = r["xy_m"]; cx, cy = r["xy_center_m"]
        e = "" if r["elev_m"] is None else f"{r['elev_m']:.1f}"
        lines.append(f"| {r['number']} | {r['symbol']} | {x:.0f}, {y:.0f} | ({cx:+.0f}, {cy:+.0f}) | {e} | {r['label']} | {r['why']} |")
    if not recs:
        lines.append("| | _no placements yet (producers land in #12, #13, #14)_ | | | | | |")
    mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jpath, mpath


def guide_sheet(playable_m: np.ndarray, water_mask: np.ndarray, sea_level_m: float, name: str,
                placements: list[Placement], path: str | Path, *, height_scale_m: float | None = None,
                max_px: int = 2000, max_rows: int = 36) -> Path:
    """Render the guide image: hillshade + 1 km grid + symbols with callout numbers,
    legend of the full symbol set, and the numbered callout table."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.colors import LightSource
    from matplotlib.patheffects import withStroke

    INK, MUTED, GRIDC = "#1f2328", "#6b7280", "#ffffff"
    n = spec.HEIGHTMAP_SIZE
    f = 4
    z = playable_m[: n - n % f, : n - n % f].reshape(n // f, f, n // f, f).mean(axis=(1, 3))
    w = water_mask[::f, ::f]
    ls = LightSource(315, 45)
    sh = ls.hillshade(z.astype(np.float64), vert_exag=2, dx=spec.PLAYABLE_M_PER_PX * f, dy=spec.PLAYABLE_M_PER_PX * f)
    rgb = np.repeat(sh[..., None], 3, axis=2) * 0.85 + 0.15
    rgb[w] = rgb[w] * 0.35 + np.array([0.23, 0.51, 0.77]) * 0.65

    dpi = 100
    fig = plt.figure(figsize=(max_px / dpi, max_px * 0.66 / dpi), dpi=dpi, facecolor="white")
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 0.62], left=0.03, right=0.99, top=0.93, bottom=0.05, wspace=0.04)
    ax = fig.add_subplot(gs[0, 0])
    ax.imshow(rgb, extent=(0, spec.PLAYABLE_SIZE_M, 0, spec.PLAYABLE_SIZE_M), interpolation="bilinear")
    # 1 km grid, labelled in km from the SW corner
    for k in range(0, int(spec.PLAYABLE_SIZE_M) + 1, 1000):
        ax.axvline(k, color=GRIDC, lw=0.5, alpha=0.45)
        ax.axhline(k, color=GRIDC, lw=0.5, alpha=0.45)
    ticks = list(range(0, 15000, 2000))
    ax.set_xticks(ticks); ax.set_yticks(ticks)
    ax.set_xticklabels([f"{t // 1000}" for t in ticks], fontsize=8, color=MUTED)
    ax.set_yticklabels([f"{t // 1000}" for t in ticks], fontsize=8, color=MUTED)
    ax.set_xlabel("km east of SW corner", fontsize=9, color=MUTED); ax.set_ylabel("km north of SW corner", fontsize=9, color=MUTED)
    ax.tick_params(length=2, colors=MUTED)
    for s in ax.spines.values():
        s.set_edgecolor("#d0d4da")
    S = spec.PLAYABLE_SIZE_M
    for txt, xy in (("N", (S / 2, S * 0.985)), ("S", (S / 2, S * 0.015)), ("W", (S * 0.015, S / 2)), ("E", (S * 0.985, S / 2))):
        ax.text(*xy, txt, ha="center", va="center", fontsize=13, weight="bold", color=INK,
                path_effects=[withStroke(linewidth=3, foreground="white")])
    ax.set_xlim(0, S); ax.set_ylim(0, S)

    # placements
    ps = sort_placements(placements)
    halo = [withStroke(linewidth=3, foreground="white")]
    for i, p in enumerate(ps, 1):
        sym = SYMBOLS[p.kind]
        if sym.line and p.geometry:
            xs = [c * spec.PLAYABLE_M_PER_PX for c, _ in p.geometry]
            ys = [(n - 1 - r) * spec.PLAYABLE_M_PER_PX for _, r in p.geometry]
            ax.plot(xs, ys, ls="--", lw=2.2, color=sym.color, path_effects=[withStroke(linewidth=4, foreground="white")])
            x, y = xs[len(xs) // 2], ys[len(ys) // 2]
        else:
            x, y = p.xy_m
            ax.plot(x, y, **_marker_kw(sym, 13), zorder=5, clip_on=False)   # edge symbols drawn whole
        # number label offset inward so it never leaves the map
        dx = -420 if x > S - 700 else 120
        dy = -360 if y > S - 600 else 120
        ax.text(x + dx, y + dy, str(i), fontsize=9, weight="bold", color=INK, path_effects=halo, zorder=6)

    # right column: legend then table
    axr = fig.add_subplot(gs[0, 1]); axr.set_axis_off()
    hs = f"  ·  height scale {height_scale_m:.0f} m" if height_scale_m else ""
    fig.suptitle(f"{name}  —  placement guide{hs}  ·  water surface {sea_level_m:.0f} m", x=0.03, ha="left",
                 fontsize=15, color=INK, weight="bold")
    handles = []
    for g in GROUP_ORDER:
        handles.append(Line2D([], [], ls="none", label=g))
        for kind, sym in SYMBOLS.items():
            if sym.group != g:
                continue
            if sym.line:
                handles.append(Line2D([], [], ls="--", lw=2, color=sym.color, label=f"   {sym.label}"))
            else:
                handles.append(Line2D([], [], **_marker_kw(sym, 9), label=f"   {sym.label}"))
    # one column so a group heading is never separated from its entries
    leg = axr.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.0, 1.0), ncol=1, fontsize=7.6, frameon=True,
                     framealpha=1, edgecolor="#d0d4da", title="Symbol key", title_fontsize=9,
                     handletextpad=0.4, borderpad=0.7, labelspacing=0.28)
    for t in leg.get_texts():
        if not t.get_text().startswith("   "):
            t.set_weight("bold"); t.set_color(INK)
        else:
            t.set_color(INK)

    y0 = 0.50
    axr.text(0, y0, "Callouts", fontsize=10, weight="bold", color=INK, transform=axr.transAxes, va="top")
    axr.text(0, y0 - 0.03, "#   what · x, y metres from SW corner (centre-relative) · elev · label — why", fontsize=7.5,
             color=MUTED, transform=axr.transAxes, va="top")
    y = y0 - 0.065
    if not ps:
        axr.text(0, y, "none yet — water sources (#12), resources (#13), connections (#14) will appear here",
                 fontsize=8.5, color=MUTED, transform=axr.transAxes, va="top", style="italic")
    # fit the list to the space: two-line rows (with the reason) while they fit, else one-line rows
    space = y - 0.015
    two_line, one_line = 0.031, 0.0175
    with_why = len(ps) * two_line <= space
    step = two_line if with_why else one_line
    rows = ps[: min(len(ps), max_rows, int(space / step))]
    for i, p in enumerate(rows, 1):
        sym = SYMBOLS[p.kind]
        x, yy = p.xy_m; cx, cy = p.xy_center_m
        e = "" if p.elev_m is None else f" · {p.elev_m:.1f} m"
        axr.text(0, y, f"{i:>2}  {sym.label} · {x:.0f}, {yy:.0f} ({cx:+.0f}, {cy:+.0f}){e} · {p.label}",
                 fontsize=7.6, color=INK, transform=axr.transAxes, va="top", family="monospace")
        if with_why and p.why:
            axr.text(0.035, y - 0.0155, p.why[:110], fontsize=6.8, color=MUTED, transform=axr.transAxes, va="top")
        y -= step
    if len(ps) > len(rows) or (ps and not with_why):
        more = f"… {len(ps) - len(rows)} more; " if len(ps) > len(rows) else ""
        axr.text(0, y, f"{more}full list with reasons in {name}_placements.md", fontsize=7.6, color=MUTED,
                 transform=axr.transAxes, va="top", style="italic")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return path
