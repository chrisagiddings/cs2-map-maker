"""Real-world terrain -> Cities: Skylines II heightmaps.

    python make_map.py --center 35.0456,-85.3097 --name chattanooga --exaggeration 1.0
    python make_map.py --bbox minx,miny,maxx,maxy --name foo --sea-level 200
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src import spec
from src.geo import Site
from src.fetch import FetchError
from src.normalize import NormalizeError
from src.export import ExportError, write_heightmap
from src.pipeline import PipelineParams, run
from src.hydro import HydroParams


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--center", help="lat,lon of the map centre")
    g.add_argument("--bbox", help="minx,miny,maxx,maxy in degrees; only its centre is used (CS2 footprint is fixed)")
    ap.add_argument("--name", required=True, help="output name -> out/<name>/")
    ap.add_argument("--exaggeration", type=float, default=1.0, help="vertical exaggeration about the water surface (default 1.0)")
    ap.add_argument("--sea-level", type=float, default=None,
                    help="in-game elevation (m above pixel 0) of the reference water surface. Default: lowest feasible.")
    ap.add_argument("--oversample", default="auto", help="playable DEM oversample: 1, 2, or auto")
    ap.add_argument("--deterrace", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--no-burn", action="store_true", help="skip river channel burning (for comparison)")
    ap.add_argument("--min-order", type=int, default=2, help="lowest stream order to carve (default 2)")
    ap.add_argument("--depth-scale", type=float, default=1.0, help="multiplier on all channel depths")
    ap.add_argument("--epsg", type=int, default=None, help="override the working metric CRS (default: local UTM)")
    ap.add_argument("--out", default="out")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.center:
        lat, lon = map(float, a.center.split(","))
        site = Site.from_center(a.name, lat, lon, a.epsg)
    else:
        site = Site.from_bbox_wgs84(a.name, *map(float, a.bbox.split(",")), epsg=a.epsg)
    p = PipelineParams(exaggeration=a.exaggeration, sea_level_m=a.sea_level, oversample=a.oversample,
                       deterrace=a.deterrace, burn=not a.no_burn,
                       hydro=HydroParams(min_order=a.min_order, depth_scale=a.depth_scale))
    out = Path(a.out) / a.name
    out.mkdir(parents=True, exist_ok=True)

    res = run(site, p)
    hm = write_heightmap(out / f"{a.name}_heightmap.png", res.playable_u16)
    wm = write_heightmap(out / f"{a.name}_worldmap.png", res.world_u16)
    res.stats["outputs"] = {"heightmap": hm.as_dict(), "worldmap": wm.as_dict()}
    (out / f"{a.name}_manifest.json").write_text(json.dumps(res.stats, indent=1, default=str))

    try:
        from src.qa import contact_sheet
        contact_sheet(res, out / f"{a.name}_qa.png")
    except ImportError:
        pass

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
    print(f"  source DEM: ~{res.stats['dem_source']['finest_ground_m']} m ({res.stats['dem_source']['finest_name']})")
    print(f"  outputs in {out}/  ({res.stats['elapsed_s']} s)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FetchError, NormalizeError, ExportError) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(2)
