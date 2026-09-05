"""Real-world terrain -> Cities: Skylines II heightmaps.

    python make_map.py --center 35.0456,-85.3097 --name chattanooga --exaggeration 1.0
    python make_map.py --bbox minx,miny,maxx,maxy --name foo --sea-level 200
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from src import spec
from src.geo import Site
from src.fetch import FetchError
from src.normalize import NormalizeError
from src.export import ExportError, write_heightmap
from src.pipeline import PipelineParams, run
from src.hydro import HydroParams
from src.publish import PublishError


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--center", help="lat,lon of the map centre")
    g.add_argument("--bbox", help="minx,miny,maxx,maxy in degrees; only its centre is used (CS2 footprint is fixed)")
    g.add_argument("--site", help="a benchmark site slug from bench/sites.json (sets centre and default name)")
    ap.add_argument("--name", default=None, help="output name -> out/<name>/ (default: the --site slug)")
    ap.add_argument("--dem-source", default=None, help="force an elevation source: 3dep, cop30, local:<name> (default: finest DTM covering the site)")
    ap.add_argument("--hydro-source", choices=["nhd", "osm"], default=None, help="force the hydrography source (default: nhd in the US, osm elsewhere)")
    ap.add_argument("--exaggeration", type=float, default=1.0, help="vertical exaggeration about the water surface (default 1.0)")
    ap.add_argument("--sea-level", type=float, default=None,
                    help="in-game elevation (m above pixel 0) of the reference water surface. "
                         "Default: the lowest value that keeps every pixel of both maps above 0.")
    ap.add_argument("--water-surface", type=float, default=None,
                    help="override the detected real-world water surface elevation (m, NAVD88). "
                         "Use when the auto-picked reference polygon is wrong; see the manifest's water_polygons_playable.")
    ap.add_argument("--oversample", default="auto",
                    help="playable DEM oversample: 1 (fetch at 3.5 m), 2 (fetch at 1.75 m, average down), or auto (2 if source < 2.5 m)")
    ap.add_argument("--deterrace", choices=["auto", "on", "off"], default="auto",
                    help="edge-preserving smoothing for integer-metre source DEMs (auto: only if > 50%% of pixels are integers)")
    ap.add_argument("--no-burn", action="store_true", help="skip river channel burning (for comparison)")
    ap.add_argument("--min-order", type=int, default=2, help="lowest NHD stream order to carve (default 2)")
    ap.add_argument("--depth-scale", type=float, default=1.0, help="multiplier on all channel depths (default 1.0)")
    ap.add_argument("--epsg", type=int, default=None, help="override the working metric CRS (default: local UTM zone)")
    ap.add_argument("--out", default="out", help="output root; files go to <out>/<name>/ (default: out)")
    ap.add_argument("--publish", action="store_true", help="after building, commit the map to the cs2-map-maker-maps repo and push")
    ap.add_argument("--maps-repo", default=None, help="maps repo checkout for --publish (default: $CS2_MAPS_REPO, else ../cs2-map-maker-maps)")
    ap.add_argument("--no-push", action="store_true", help="with --publish: commit locally only")
    return ap.parse_args(_join_negative_values(sys.argv[1:] if argv is None else list(argv)))


def _join_negative_values(argv: list[str]) -> list[str]:
    """argparse reads '-85.3,35.0' as an option name. Fold such values into
    '--flag=value' so western-hemisphere coordinates work without quoting tricks."""
    out, i = [], 0
    while i < len(argv):
        tok = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if tok in ("--center", "--bbox") and nxt is not None and nxt.startswith("-") and any(c.isdigit() for c in nxt):
            out.append(f"{tok}={nxt}")
            i += 2
        else:
            out.append(tok)
            i += 1
    return out


def _git_commit() -> str | None:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True,
                                       cwd=Path(__file__).parent, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def main(argv=None) -> int:
    a = parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # place names are not cp1252
    except Exception:
        pass
    if a.site:
        sites = {s["slug"]: s for s in json.loads((Path(__file__).parent / "bench" / "sites.json").read_text(encoding="utf-8"))}
        if a.site not in sites:
            raise SystemExit(f"unknown --site {a.site!r}; known: {', '.join(sorted(sites))}")
        s = sites[a.site]
        a.name = a.name or a.site
        site = Site.from_center(a.name, s["lat"], s["lon"], a.epsg)
    elif a.center:
        if not a.name:
            raise SystemExit("--name is required with --center")
        lat, lon = map(float, a.center.split(","))
        site = Site.from_center(a.name, lat, lon, a.epsg)
    else:
        if not a.name:
            raise SystemExit("--name is required with --bbox")
        site = Site.from_bbox_wgs84(a.name, *map(float, a.bbox.split(",")), epsg=a.epsg)
    p = PipelineParams(exaggeration=a.exaggeration, sea_level_m=a.sea_level, water_surface_real_m=a.water_surface,
                       oversample=a.oversample, deterrace=a.deterrace, burn=not a.no_burn,
                       hydro=HydroParams(min_order=a.min_order, depth_scale=a.depth_scale),
                       dem_source=a.dem_source, hydro_source=a.hydro_source)
    out = Path(a.out) / a.name
    out.mkdir(parents=True, exist_ok=True)

    res = run(site, p)
    hm = write_heightmap(out / f"{a.name}_heightmap.png", res.playable_u16)
    wm = write_heightmap(out / f"{a.name}_worldmap.png", res.world_u16)
    res.stats["outputs"] = {"heightmap": hm.as_dict(), "worldmap": wm.as_dict()}
    res.stats["run"] = {"argv": sys.argv[1:] if argv is None else list(argv), "args": vars(a),
                        "generated": datetime.now().isoformat(timespec="seconds"), "git_commit": _git_commit(),
                        "cs2_spec": {"heightmap_px": spec.HEIGHTMAP_SIZE, "playable_m": spec.PLAYABLE_SIZE_M,
                                     "world_m": spec.WORLD_SIZE_M, "pixel_65535_m": res.stats["vertical"]["height_scale_m"]}}
    (out / f"{a.name}_manifest.json").write_text(json.dumps(res.stats, indent=1, default=str))

    from src.qa import contact_sheet
    qa_path = contact_sheet(res, out / f"{a.name}_qa.png")
    from src.guide import guide_sheet, write_placements
    v0 = res.stats["vertical"]
    guide_path = guide_sheet(res.playable_m, res.water_mask, v0["sea_level_m"], a.name, res.placements,
                             out / f"{a.name}_guide.png", height_scale_m=v0["height_scale_m"])
    write_placements(res.placements, out / a.name, {"name": a.name, "height_scale_m": v0["height_scale_m"],
                                                    "sea_level_m": v0["sea_level_m"], "centre": [site.lat, site.lon]})

    v = res.stats["vertical"]
    pl = res.stats["playable"]
    print("\n" + "=" * 66)
    print(f"  {a.name}: HEIGHT SCALE TO TYPE INTO THE CS2 EDITOR:  {v['height_scale_m']:.0f} m")
    print("=" * 66)
    print(f"  water surface ({v['water_surface_source']}): real {v['water_surface_real_m']:.1f} m -> in-game {v['sea_level_m']:.1f} m")
    print(f"  playable elevation: real {pl['min_real_m']:.0f}..{pl['max_real_m']:.0f} m (relief {pl['relief_m']:.0f} m), "
          f"in-game {pl['min_cs2_m']:.0f}..{pl['max_cs2_m']:.0f} m, exaggeration x{v['exaggeration']}")
    print(f"  buildable (<10% slope): {pl['buildable_fraction_land']:.1%} of land, {pl['buildable_fraction_all']:.1%} of all")
    print(f"  pixel range: heightmap {pl['px_min']}..{pl['px_max']}, worldmap {res.stats['world']['px_min']}..{res.stats['world']['px_max']}  "
          f"(1 level = {v['m_per_level'] * 100:.1f} cm)")
    ds = res.stats["dem_source"]
    print(f"  source DEM: ~{ds['finest_ground_m']} m {ds['kind'].upper()} ({ds['finest_name']}, {ds['resample']})"
          + (f"; world from {ds['world']['source']}" if ds.get("mixed_sources") else "")
          + f"; hydrography: {res.stats['hydro_source']['id']}")
    print(f"  QA sheet: {qa_path}")
    print(f"  guide:    {guide_path}  ({len(res.placements)} placements)")
    ws = [pl for pl in res.placements if pl.kind.startswith("water.")]
    if ws:
        print("\n  WATER SOURCES (editor: x, y metres from the map centre; level in in-game metres)")
        from src.guide import SYMBOLS
        for i, pl in enumerate(ws, 1):
            cx, cy = pl.xy_center_m
            lv = "" if pl.elev_m is None else f"level {pl.elev_m:6.1f} m"
            print(f"   {i:>2}. {SYMBOLS[pl.kind].label:<22} {cx:+8.0f}, {cy:+8.0f}   {lv}   {pl.label}")
    print(f"  outputs in {out}/  ({res.stats['elapsed_s']} s)")
    if a.publish:
        from src.publish import publish
        r = publish(out, a.maps_repo, push=not a.no_push)
        print(f"  published: {r.folder}  commit {r.commit}  pushed={r.pushed}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FetchError, NormalizeError, ExportError, PublishError) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(2)
