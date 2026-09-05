"""Commit a finished map from out/<name>/ into the cs2-map-maker-maps repo.

    python publish_map.py --name chattanooga
    python publish_map.py --name chattanooga --maps-repo D:\\maps --no-push
    python publish_map.py --name chattanooga --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.publish import publish, PublishError


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="map name used with make_map.py (folder under --out)")
    ap.add_argument("--out", default="out", help="output root used by make_map.py (default: out)")
    ap.add_argument("--maps-repo", default=None,
                    help="path to the cs2-map-maker-maps checkout (default: $CS2_MAPS_REPO, else ../cs2-map-maker-maps)")
    ap.add_argument("--message", default=None, help="commit message (default: generated from the manifest)")
    ap.add_argument("--no-push", action="store_true", help="commit locally but do not push")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written and stop")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    r = publish(Path(a.out) / a.name, a.maps_repo, message=a.message, push=not a.no_push, dry_run=a.dry_run)
    print(f"\n  published: {r.folder}")
    print(f"  files:     {', '.join(r.files)}")
    print(f"  commit:    {r.commit or '(dry run)'}   pushed: {r.pushed}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PublishError as e:
        print(f"\nPUBLISH ERROR: {e}", file=sys.stderr)
        sys.exit(2)
