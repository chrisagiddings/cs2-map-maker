"""Stage 2 driver: fetch and cache everything for one site, print what came back,
and render a quick preview PNG of the raw DEMs + hydrography.

    python tools/fetch_site.py --center 35.0456,-85.3097 --name chattanooga
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import spec                                    # noqa: E402
from src.geo import Site                                # noqa: E402
from src.fetch import fetch_dem, fetch_nhd, dem_source_info, FetchError   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--center", help="lat,lon")
    ap.add_argument("--bbox", help="minx,miny,maxx,maxy in degrees (centre is used)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--oversample", default="auto",
                    help="playable DEM oversample factor (1, 2, or auto: 2 if source < 2.5 m)")
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    if args.center:
        lat, lon = map(float, args.center.split(","))
        site = Site.from_center(args.name, lat, lon)
    elif args.bbox:
        site = Site.from_bbox_wgs84(args.name, *map(float, args.bbox.split(",")))
    else:
        ap.error("need --center or --bbox")

    print(json.dumps(site.describe(), indent=1))
    t0 = time.time()

    info = dem_source_info(site, site.playable_bbox)
    print(f"\n3DEP source over playable area: finest ~{info.finest_ground_m} m ground ({info.finest_name})")
    print("  datasets:", ", ".join(info.datasets))
    if args.oversample == "auto":
        over = 2 if info.finest_ground_m < 2.5 else 1
    else:
        over = int(args.oversample)
    print(f"  playable oversample: {over}x -> {spec.PLAYABLE_M_PER_PX / over} m/px")

    play, play_tf, play_path = fetch_dem(site, site.playable_bbox, spec.PLAYABLE_M_PER_PX / over, label="playable")
    world, world_tf, world_path = fetch_dem(site, site.world_bbox, spec.WORLD_M_PER_PX, label="world")

    for lbl, a, p in (("playable", play, play_path), ("world", world, world_path)):
        v = a[np.isfinite(a)]
        print(f"\n[{lbl}] {a.shape[1]}x{a.shape[0]} px  valid {np.isfinite(a).mean():.2%}  "
              f"min {v.min():.1f} m  max {v.max():.1f} m  relief {v.max() - v.min():.1f} m  -> {p.name}")

    flow = fetch_nhd(site, site.world_bbox, "flowline",
                     out_fields="permanent_identifier,gnis_name,streamorde,ftype,fcode,lengthkm,totdasqkm")
    area = fetch_nhd(site, site.world_bbox, "area", out_fields="permanent_identifier,gnis_name,ftype,fcode,areasqkm", required=False)
    wb = fetch_nhd(site, site.world_bbox, "waterbody", out_fields="permanent_identifier,gnis_name,ftype,fcode,areasqkm", required=False)

    print(f"\n[nhd] flowlines {len(flow)}  areas {len(area)}  waterbodies {len(wb)}")
    orders = flow["streamorde"].value_counts().sort_index()
    print("  stream order histogram:", dict(orders))
    named = flow[flow["gnis_name"].notna()].groupby("gnis_name")["streamorde"].max().sort_values(ascending=False)
    print("  biggest named streams:", ", ".join(f"{n} (order {int(o)})" for n, o in named.head(8).items()))
    if len(area):
        print("  named areas:", ", ".join(sorted({str(n) for n in area["gnis_name"].dropna()})[:10]))
    if len(wb):
        big = wb.sort_values("areasqkm", ascending=False).head(5)
        print("  largest waterbodies:", ", ".join(f"{n} ({a:.1f} km2)" for n, a in zip(big["gnis_name"], big["areasqkm"])))

    print(f"\ndone in {time.time() - t0:.0f}s")

    if not args.no_preview:
        preview(site, play, world, flow, area, wb, Path("out") / site.name / f"{site.name}_stage2_preview.png")


def preview(site, play, world, flow, area, wb, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource

    out.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(16, 8.5), dpi=110)
    ls = LightSource(azdeg=315, altdeg=45)
    for ax, (lbl, a, bb, mpp) in zip(axes, (("playable (raw DEM)", play, site.playable_bbox, None),
                                            ("world (raw DEM)", world, site.world_bbox, spec.WORLD_M_PER_PX))):
        # downsample for display
        step = max(1, a.shape[0] // 1024)
        d = a[::step, ::step]
        d = np.where(np.isfinite(d), d, np.nanmin(d))
        ext = (bb.minx, bb.maxx, bb.miny, bb.maxy)
        dx = (bb.width / d.shape[1])
        rgb = ls.shade(d, cmap=plt.cm.gist_earth, blend_mode="soft", vert_exag=1.5, dx=dx, dy=dx)
        ax.imshow(rgb, extent=ext)
        if len(flow):
            big = flow[flow["streamorde"] >= 3]
            big.plot(ax=ax, color="deepskyblue", linewidth=0.4 + 0.25 * (big["streamorde"] - 3))
        if len(wb):
            wb.plot(ax=ax, facecolor="dodgerblue", edgecolor="none", alpha=0.6)
        if len(area):
            area.plot(ax=ax, facecolor="royalblue", edgecolor="none", alpha=0.7)
        pb = site.playable_bbox
        ax.plot([pb.minx, pb.maxx, pb.maxx, pb.minx, pb.minx], [pb.miny, pb.miny, pb.maxy, pb.maxy, pb.miny], "r-", lw=1)
        ax.set_xlim(bb.minx, bb.maxx); ax.set_ylim(bb.miny, bb.maxy)
        ax.set_title(f"{site.name}: {lbl}  [{np.nanmin(a):.0f}–{np.nanmax(a):.0f} m]")
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(f"Stage 2 raw data preview — EPSG:{site.epsg} — blue = NHD flowlines (order>=3) / waterbodies / river areas; red = playable footprint")
    fig.tight_layout()
    fig.savefig(out)
    print(f"preview -> {out}")


if __name__ == "__main__":
    try:
        main()
    except FetchError as e:
        print(f"\nFETCH ERROR: {e}", file=sys.stderr)
        sys.exit(2)
