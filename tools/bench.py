"""Benchmark harness (issue #19).

    python tools/bench.py run [--only chisinau,lviv] [--exaggeration 1.0] [--publish]
    python tools/bench.py compare <results dir or json>     # deltas vs an older results set
    python tools/bench.py check                             # latest results vs expected ranges in sites.json

Each `run` builds every site with make_map.py, pulls a scorecard from its manifest into
bench/results/<slug>.json (a list of runs keyed by git commit + date), writes 600 px thumbnails
under bench/thumbs/, and rewrites bench/results.md. Non-US sites are listed first on purpose.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
SITES = BENCH / "sites.json"
RESULTS = BENCH / "results"
THUMBS = BENCH / "thumbs"
PY = sys.executable

# regression thresholds for `compare`
BUILDABLE_PTS = 2.0
SEAM_FACTOR = 2.0


def load_sites() -> list[dict]:
    return json.loads(SITES.read_text(encoding="utf-8"))


def save_sites(sites: list[dict]) -> None:
    SITES.write_text(json.dumps(sites, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def scorecard(manifest: dict) -> dict:
    v, pl, ds = manifest["vertical"], manifest["playable"], manifest["dem_source"]
    ws = manifest.get("water_sources", [])
    kinds = {}
    for w in ws:
        k = w["kind"].split(".", 1)[1]
        kinds[k] = kinds.get(k, 0) + 1
    hist = manifest.get("slope_histogram", {}).get("fraction", [])
    return {
        "dem_source": ds.get("source"), "dem_product": ds.get("finest_name"), "dem_kind": ds.get("kind"),
        "dem_native_m": ds.get("finest_ground_m"), "dem_resample": ds.get("resample"),
        "mixed_sources": bool(ds.get("mixed_sources")),
        "hydro_source": manifest.get("hydro_source", {}).get("id"),
        "flowlines": manifest.get("hydro_source", {}).get("flowlines"),
        "culverted": manifest.get("hydro_source", {}).get("culverted"),
        "nodata_filled": manifest.get("nodata_filled_fraction", {}),
        "terracing_playable": manifest["terracing"]["playable_integer_fraction"],
        "deterraced": manifest["terracing"]["deterraced"],
        "relief_m": pl["relief_m"], "min_real_m": pl["min_real_m"], "max_real_m": pl["max_real_m"],
        "height_scale_m": v["height_scale_m"], "m_per_level": v["m_per_level"],
        "sea_level_m": v["sea_level_m"], "water_surface_real_m": v["water_surface_real_m"],
        "water_surface_source": v.get("water_surface_source"),
        "buildable_land": pl["buildable_fraction_land"], "water_fraction": manifest["water_fraction"]["playable"],
        "water_sources": len(ws), "water_source_kinds": kinds,
        "seam_m": manifest.get("world_center_mean_abs_diff_before_replace_m"),
        "slope_hist": hist,
        "elapsed_s": manifest.get("elapsed_s"), "timings_s": manifest.get("timings_s", {}),
        "downloaded_mb": round(manifest.get("downloaded", {}).get("bytes", 0) / 1e6, 1),
    }


def thumbnail(src: Path, dst: Path, width: int = 600) -> None:
    from PIL import Image
    im = Image.open(src).convert("RGB")
    h = int(im.height * width / im.width)
    im.resize((width, h), Image.LANCZOS).save(dst, "JPEG", quality=80, optimize=True)


def latest(slug: str) -> dict | None:
    f = RESULTS / f"{slug}.json"
    if not f.exists():
        return None
    runs = json.loads(f.read_text(encoding="utf-8"))
    return runs[-1] if runs else None


def order_sites(sites: list[dict]) -> list[dict]:
    """Non-US first, then US: the benchmark must not be read US-first."""
    return sorted(sites, key=lambda s: (s["country"] == "US", s["slug"]))


# ----------------------------------------------------------------------------
# run
# ----------------------------------------------------------------------------
def cmd_run(a) -> int:
    sites = order_sites(load_sites())
    only = set(a.only.split(",")) if a.only else None
    RESULTS.mkdir(parents=True, exist_ok=True); THUMBS.mkdir(parents=True, exist_ok=True)
    commit = git_commit()
    failures = []
    for s in sites:
        slug = s["slug"]
        if only and slug not in only:
            continue
        name = f"bench_{slug}"
        cmd = [PY, "-u", str(ROOT / "make_map.py"), "--site", slug, "--name", name, "--exaggeration", str(a.exaggeration)]
        if a.publish:
            cmd.append("--publish")
        print(f"\n=== {slug} ({s['name']}, {s['country']}) ===", flush=True)
        t0 = time.time()
        log = ROOT / "out" / f"{name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "w", encoding="utf-8") as lf:
            r = subprocess.run(cmd, cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT, text=True,
                               env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        if r.returncode != 0:
            print(f"  FAILED ({time.time() - t0:.0f}s): " + " | ".join(tail), flush=True)
            failures.append(slug)
            _append_result(slug, {"commit": commit, "date": datetime.now().isoformat(timespec="seconds"),
                                  "exaggeration": a.exaggeration, "failed": True, "error": tail[-1] if tail else "?"})
            continue
        man = json.loads((ROOT / "out" / name / f"{name}_manifest.json").read_text(encoding="utf-8"))
        card = scorecard(man)
        card.update(commit=commit, date=datetime.now().isoformat(timespec="seconds"), exaggeration=a.exaggeration, failed=False)
        _append_result(slug, card)
        for kind in ("qa", "guide"):
            src = ROOT / "out" / name / f"{name}_{kind}.png"
            if src.exists():
                thumbnail(src, THUMBS / f"{slug}_{kind}.jpg")
        print(f"  ok ({time.time() - t0:.0f}s): scale {card['height_scale_m']:.0f} m, buildable {card['buildable_land']:.0%}, "
              f"relief {card['relief_m']:.0f} m, {card['water_sources']} water sources, {card['dem_source']}/{card['hydro_source']}, "
              f"{card['downloaded_mb']} MB downloaded", flush=True)
    write_results_md(load_sites())
    print(f"\nresults -> {BENCH / 'results.md'}" + (f"   FAILED: {', '.join(failures)}" if failures else ""))
    return 1 if failures else 0


def _append_result(slug: str, card: dict) -> None:
    f = RESULTS / f"{slug}.json"
    runs = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    runs.append(card)
    f.write_text(json.dumps(runs[-50:], indent=1, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------------------
# results.md
# ----------------------------------------------------------------------------
def write_results_md(sites: list[dict]) -> None:
    rows = ["# Benchmark results", "",
            "One row per site, latest run. Non-US sites first. Scorecards live in `results/<slug>.json`; "
            "expected ranges in `sites.json`. Regenerated by `python tools/bench.py run`.", "",
            "| Site | Source | Kind | Native | Hydro | Relief | Height scale | Sea | Buildable | Water | Sources | Seam | Terr. | Time | DL | Run |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    cards = []
    for s in order_sites(sites):
        c = latest(s["slug"])
        if c is None:
            rows.append(f"| {s['name']} ({s['country']}) | _not run_ | | | | | | | | | | | | | | |")
            continue
        if c.get("failed"):
            rows.append(f"| {s['name']} ({s['country']}) | **FAILED** | | | | | | | | | | | | | | {c['commit']} |")
            continue
        cards.append((s, c))
        rows.append(
            f"| {s['name']} ({s['country']}) | {c['dem_source']} | {c['dem_kind']} | {c['dem_native_m']} m | {c['hydro_source']} | "
            f"{c['relief_m']:.0f} m | **{c['height_scale_m']:.0f} m** | {c['sea_level_m']:.0f} m | {c['buildable_land']:.0%} | "
            f"{c['water_fraction']:.1%} | {c['water_sources']} | {c['seam_m']:.2f} m | {c['terracing_playable']:.0%} | "
            f"{c['elapsed_s']:.0f} s | {c['downloaded_mb']:.0f} MB | {c['commit']} {c['date'][:10]} |")
    rows += ["", "## Sheets", ""]
    for s, c in cards:
        rows += [f"### {s['name']}, {s['country']}  ·  `{s['slug']}`", "",
                 f"{s['notes']}. Water surface: {c['water_surface_source']}. "
                 f"Stages: {', '.join(f'{k} {v}s' for k, v in c.get('timings_s', {}).items())}.", "",
                 f"| QA | Guide |", "|---|---|",
                 f"| ![qa](thumbs/{s['slug']}_qa.jpg) | ![guide](thumbs/{s['slug']}_guide.jpg) |", ""]
    (BENCH / "results.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------
# compare / check
# ----------------------------------------------------------------------------
def _deltas(old: dict, new: dict) -> list[str]:
    out = []
    if old.get("failed") or new.get("failed"):
        if bool(old.get("failed")) != bool(new.get("failed")):
            out.append("failed" if new.get("failed") else "now builds")
        return out
    b = (new["buildable_land"] - old["buildable_land"]) * 100
    if abs(b) > BUILDABLE_PTS:
        out.append(f"buildable {b:+.1f} pts")
    if new["height_scale_m"] != old["height_scale_m"]:
        out.append(f"height scale {old['height_scale_m']:.0f} -> {new['height_scale_m']:.0f} m")
    if new["water_sources"] != old["water_sources"]:
        out.append(f"water sources {old['water_sources']} -> {new['water_sources']}")
    if old.get("seam_m") and new.get("seam_m") and new["seam_m"] > SEAM_FACTOR * old["seam_m"] and new["seam_m"] > 0.2:
        out.append(f"seam {old['seam_m']:.2f} -> {new['seam_m']:.2f} m")
    if new.get("dem_source") != old.get("dem_source") or new.get("hydro_source") != old.get("hydro_source"):
        out.append(f"sources {old.get('dem_source')}/{old.get('hydro_source')} -> {new.get('dem_source')}/{new.get('hydro_source')}")
    return out


def cmd_compare(a) -> int:
    base = Path(a.baseline)
    drift = 0
    for s in order_sites(load_sites()):
        slug = s["slug"]
        new = latest(slug)
        f = base / f"{slug}.json" if base.is_dir() else base
        if new is None or not f.exists():
            continue
        runs = json.loads(f.read_text(encoding="utf-8"))
        old = runs[-1] if isinstance(runs, list) else runs.get(slug)
        if not old:
            continue
        d = _deltas(old, new)
        flag = "DRIFT" if d else "ok"
        drift += bool(d)
        print(f"{slug:12s} {flag:5s} {old.get('commit')} -> {new.get('commit')}  {'; '.join(d)}")
    return 1 if drift else 0


def _check_range(name: str, value, rng) -> str | None:
    if value is None or rng is None:
        return None
    lo, hi = rng
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        return f"{name} {value:.3g} outside [{lo}, {hi}]"
    return None


def cmd_check(a) -> int:
    bad = 0
    for s in order_sites(load_sites()):
        c = latest(s["slug"])
        exp = s.get("expected")
        if c is None:
            print(f"{s['slug']:12s} not run"); continue
        if c.get("failed"):
            print(f"{s['slug']:12s} FAILED: {c.get('error')}"); bad += 1; continue
        if not exp:
            print(f"{s['slug']:12s} no expected ranges yet (review the QA sheet, then fill sites.json)"); continue
        probs = [x for x in (
            _check_range("buildable", c["buildable_land"], exp.get("buildable_land")),
            _check_range("height_scale", c["height_scale_m"], exp.get("height_scale_m")),
            _check_range("relief", c["relief_m"], exp.get("relief_m")),
            _check_range("water_fraction", c["water_fraction"], exp.get("water_fraction")),
            _check_range("water_sources", c["water_sources"], exp.get("water_sources")),
            _check_range("seam", c["seam_m"], [None, exp.get("seam_m_max")] if exp.get("seam_m_max") is not None else None),
        ) if x]
        if exp.get("dem_source") and c["dem_source"] != exp["dem_source"]:
            probs.append(f"dem source {c['dem_source']} != {exp['dem_source']}")
        if exp.get("hydro_source") and c["hydro_source"] != exp["hydro_source"]:
            probs.append(f"hydro source {c['hydro_source']} != {exp['hydro_source']}")
        bad += bool(probs)
        print(f"{s['slug']:12s} {'FAIL' if probs else 'ok  '} {'; '.join(probs)}")
    return 1 if bad else 0


def cmd_expect(a) -> int:
    """Seed expected ranges from the latest run with generous margins; edit by hand after review."""
    sites = load_sites()
    for s in sites:
        c = latest(s["slug"])
        if c is None or c.get("failed") or (s.get("expected") and not a.force):
            continue
        s["expected"] = {
            "dem_source": c["dem_source"], "hydro_source": c["hydro_source"],
            "buildable_land": [round(max(0, c["buildable_land"] - 0.05), 3), round(min(1, c["buildable_land"] + 0.05), 3)],
            "height_scale_m": [c["height_scale_m"] * 0.9, c["height_scale_m"] * 1.1],
            "relief_m": [round(c["relief_m"] * 0.9), round(c["relief_m"] * 1.1)],
            "water_fraction": [round(max(0, c["water_fraction"] - 0.02), 3), round(c["water_fraction"] + 0.02, 3)],
            "water_sources": [max(0, c["water_sources"] - 3), c["water_sources"] + 3],
            "seam_m_max": round(max(0.5, (c["seam_m"] or 0) * 2), 2),
            "reviewed": False, "review_note": "seeded from first run; review the QA sheet and tighten",
        }
    save_sites(sites)
    print("expected ranges seeded for sites without them; set reviewed=true after looking at each QA sheet")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--only"); r.add_argument("--exaggeration", type=float, default=1.0); r.add_argument("--publish", action="store_true")
    c = sub.add_parser("compare"); c.add_argument("baseline", help="older bench/results dir (or one site json)")
    sub.add_parser("check")
    e = sub.add_parser("expect"); e.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "compare": cmd_compare, "check": cmd_check, "expect": cmd_expect}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
