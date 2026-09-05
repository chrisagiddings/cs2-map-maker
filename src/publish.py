"""Publish a finished map into the cs2-map-maker-maps repo.

Folder per map: "{Display name} - {hash}", where the hash comes from the
inputs that define the map (centre, CRS, vertical and channel settings), not
from file bytes. Same configuration -> same folder (update); any terrain-
changing tweak -> new folder. The pipeline's git commit is deliberately NOT
part of the hash (see issue #1).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .export import verify_heightmap, ExportError

INDEX_START = "<!-- maps:start -->"
INDEX_END = "<!-- maps:end -->"
DEFAULT_MAPS_REPO = Path(__file__).resolve().parent.parent.parent / "cs2-map-maker-maps"


class PublishError(RuntimeError):
    pass


# ----------------------------------------------------------------------------
# naming
# ----------------------------------------------------------------------------
def hash_inputs(manifest: dict) -> dict:
    """The subset of the manifest that defines the terrain."""
    site, p = manifest["site"], manifest["params"]
    h = p.get("hydro", {})
    return {
        "lat": round(float(site["lat"]), 4), "lon": round(float(site["lon"]), 4), "epsg": int(site["epsg"]),
        "exaggeration": float(p.get("exaggeration", 1.0)),
        "sea_level_m": p.get("sea_level_m"),
        "water_surface_real_m": p.get("water_surface_real_m"),
        "burn": bool(p.get("burn", True)),
        "min_order": h.get("min_order"), "depth_scale": h.get("depth_scale"),
        "deterrace": p.get("deterrace"), "oversample": str(p.get("oversample")),
    }


def map_hash(manifest: dict) -> str:
    canon = json.dumps(hash_inputs(manifest), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canon.encode()).hexdigest()[:7]


def display_name(manifest: dict) -> str:
    dn = manifest.get("display_name")
    if dn:
        return str(dn).strip()
    slug = manifest["site"]["name"]
    return " ".join(w.capitalize() for w in slug.replace("_", " ").replace("-", " ").split())


def folder_name(manifest: dict) -> str:
    return f"{display_name(manifest)} - {map_hash(manifest)}"


def file_stem(manifest: dict) -> str:
    return display_name(manifest).replace(" ", "_")


# ----------------------------------------------------------------------------
# git helpers
# ----------------------------------------------------------------------------
def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise PublishError(f"git {' '.join(args)} failed in {repo}:\n{r.stderr.strip() or r.stdout.strip()}")
    return r


def _check_repo(repo: Path) -> None:
    if not (repo / ".git").exists():
        raise PublishError(f"{repo} is not a git checkout. Clone cs2-map-maker-maps there or pass --maps-repo.")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        raise PublishError(f"{repo} has uncommitted changes; commit or stash them first:\n{dirty}")
    for key in ("user.name", "user.email"):
        if _git(repo, "config", key, check=False).returncode != 0 and not os.environ.get("GIT_AUTHOR_NAME"):
            raise PublishError(f"git {key} is not set; run: git config --global {key} \"...\"")


def _lfs_tracked(repo: Path) -> bool:
    ga = repo / ".gitattributes"
    return ga.exists() and "filter=lfs" in ga.read_text(encoding="utf-8")


def _lfs_available(repo: Path) -> bool:
    return _git(repo, "lfs", "version", check=False).returncode == 0


def _lfs_check(repo: Path, folder: Path, progress) -> None:
    """After commit: every heightmap in the folder must be an LFS pointer, not a blob."""
    rel = str(folder.relative_to(repo)).replace("\\", "/")
    ls = _git(repo, "lfs", "ls-files", "--name-only", check=False).stdout
    tracked = {line.strip().replace("\\", "/") for line in ls.splitlines() if line.strip()}
    expected = [p for p in folder.glob("*_heightmap.png")] + [p for p in folder.glob("*_worldmap.png")]
    missing = [p.name for p in expected if f"{rel}/{p.name}" not in tracked]
    if missing:
        raise PublishError(f"{', '.join(missing)} were committed as plain blobs, not LFS objects. "
                           f"Is git-lfs installed (`git lfs install`)? Fix with `git lfs migrate import` before pushing.")
    progress(f"[publish] LFS: {len(expected)} heightmap(s) stored as LFS objects")


# ----------------------------------------------------------------------------
# content
# ----------------------------------------------------------------------------
def map_readme(manifest: dict, stem: str) -> str:
    v, pl, site = manifest["vertical"], manifest["playable"], manifest["site"]
    run = manifest.get("run", {})
    src = manifest.get("dem_source", {})
    hyd = (manifest.get("water_polygons_playable") or [{}])[0]
    return f"""# {display_name(manifest)}

![QA sheet]({stem}_qa.png)

| | |
|---|---|
| **Height scale (type into the CS2 editor)** | **{v['height_scale_m']:.0f} m** |
| Water surface, in-game | {v['sea_level_m']:.1f} m |
| Water surface, real (NAVD88) | {v['water_surface_real_m']:.1f} m ({hyd.get('name', 'n/a')}) |
| Centre | {site['lat']:.4f}, {site['lon']:.4f} (EPSG:{site['epsg']}) |
| Playable elevation, real | {pl['min_real_m']:.0f} – {pl['max_real_m']:.0f} m (relief {pl['relief_m']:.0f} m) |
| Vertical exaggeration | ×{v['exaggeration']:.2f} |
| Buildable (< 10 % slope) | {pl['buildable_fraction_land']:.1%} of land |
| Water coverage | {manifest['water_fraction']['playable']:.1%} of playable |
| Channel burning | {'on' if manifest['params'].get('burn', True) else 'off'} |
| Source DEM | ~{src.get('finest_ground_m', '?')} m ({src.get('finest_name', '?')}) |

## Import

1. Copy `{stem}_heightmap.png` and `{stem}_worldmap.png` to
   `%USERPROFILE%\\AppData\\LocalLow\\Colossal Order\\Cities Skylines II\\Heightmaps\\`.
2. In the map editor import the heightmap, then the world map.
3. Set the height scale to **{v['height_scale_m']:.0f}**.

## Provenance

- Built by [cs2-map-maker](https://github.com/chrisagiddings/cs2-map-maker) commit `{run.get('git_commit') or 'unknown'}` on {run.get('generated', 'unknown')}
- Command: `python make_map.py {' '.join(run.get('argv', []))}`
- Map hash `{map_hash(manifest)}` (from centre, CRS, vertical and channel settings; see `{stem}_manifest.json`)
- Elevation: USGS 3DEP · Hydrography: USGS NHDPlus HR
"""


def _index_rows(repo: Path) -> list[str]:
    rows = []
    for d in sorted(p for p in repo.iterdir() if p.is_dir() and not p.name.startswith(".")):
        mans = list(d.glob("*_manifest.json"))
        if not mans:
            continue
        try:
            m = json.loads(mans[0].read_text(encoding="utf-8"))
        except Exception:
            continue
        v, pl, site = m["vertical"], m["playable"], m["site"]
        stem = mans[0].name[: -len("_manifest.json")]
        gen = (m.get("run", {}).get("generated") or "")[:10]
        rows.append(f"| [{d.name}]({d.name.replace(' ', '%20')}/) | {site['lat']:.4f}, {site['lon']:.4f} | "
                    f"**{v['height_scale_m']:.0f} m** | {v['sea_level_m']:.0f} m | ×{v['exaggeration']:.2f} | "
                    f"{pl['buildable_fraction_land']:.0%} | {pl['relief_m']:.0f} m | "
                    f"[QA]({d.name.replace(' ', '%20')}/{stem}_qa.png) | {gen} |")
    return rows


def update_index(repo: Path) -> None:
    readme = repo / "README.md"
    # errors="replace": a README damaged by an earlier non-UTF-8 write must not block publishing;
    # the damaged part is inside the index block, which is regenerated below.
    text = readme.read_text(encoding="utf-8", errors="replace") if readme.exists() else "# cs2-map-maker-maps\n"
    header = ("| Map | Centre | Height scale | Sea level | Exag. | Buildable | Relief | QA | Published |\n"
              "|---|---|---|---|---|---|---|---|---|")
    rows = _index_rows(repo)
    table = header + "\n" + ("\n".join(rows) if rows else "| _none published yet_ | | | | | | | | |")
    block = f"{INDEX_START}\n{table}\n{INDEX_END}"
    if INDEX_START in text and INDEX_END in text:
        pre, rest = text.split(INDEX_START, 1)
        _, post = rest.split(INDEX_END, 1)
        text = pre + block + post
    elif "## Maps" in text:
        pre, _ = text.split("## Maps", 1)
        text = pre + "## Maps\n\n" + block + "\n"
    else:
        text = text.rstrip() + "\n\n## Maps\n\n" + block + "\n"
    readme.write_text(text, encoding="utf-8")


# ----------------------------------------------------------------------------
# publish
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Published:
    folder: Path
    commit: str | None
    files: list[str]
    pushed: bool


def publish(out_dir: str | os.PathLike, maps_repo: str | os.PathLike | None = None, *,
            message: str | None = None, push: bool = True, dry_run: bool = False,
            progress=print) -> Published:
    out_dir = Path(out_dir)
    repo = Path(maps_repo or os.environ.get("CS2_MAPS_REPO") or DEFAULT_MAPS_REPO).resolve()
    mans = list(out_dir.glob("*_manifest.json"))
    if len(mans) != 1:
        raise PublishError(f"expected exactly one *_manifest.json in {out_dir}, found {len(mans)}")
    manifest = json.loads(mans[0].read_text(encoding="utf-8"))
    name = manifest["site"]["name"]
    if "outputs" not in manifest:
        raise PublishError(f"{mans[0].name} has no 'outputs' block; the build did not finish")
    src_files = {
        "heightmap": out_dir / f"{name}_heightmap.png",
        "worldmap": out_dir / f"{name}_worldmap.png",
        "qa": out_dir / f"{name}_qa.png",
        "manifest": mans[0],
    }
    for k, f in src_files.items():
        if not f.exists():
            raise PublishError(f"missing {k}: {f}")
    # never commit a bad PNG: independent read-back of both heightmaps
    try:
        verify_heightmap(src_files["heightmap"])
        verify_heightmap(src_files["worldmap"])
    except ExportError as e:
        raise PublishError(f"refusing to publish: {e}") from e

    _check_repo(repo)
    lfs = _lfs_tracked(repo)
    if lfs and not _lfs_available(repo):
        raise PublishError(f"{repo.name} tracks heightmaps with Git LFS but git-lfs is not installed. "
                           f"Install it (bundled with Git for Windows) and run `git lfs install`.")
    if not lfs:
        progress(f"[publish] WARNING: {repo.name} does not track PNGs with Git LFS; each map adds ~60 MB of "
                 f"plain blobs. Run in the maps repo: git lfs install && git lfs track \"*_heightmap.png\" \"*_worldmap.png\"")

    folder = repo / folder_name(manifest)
    stem = file_stem(manifest)
    plan = {
        folder / f"{stem}_heightmap.png": src_files["heightmap"],
        folder / f"{stem}_worldmap.png": src_files["worldmap"],
        folder / f"{stem}_qa.png": src_files["qa"],
        folder / f"{stem}_manifest.json": src_files["manifest"],
    }
    res_dir = out_dir / "resources"
    if res_dir.is_dir():
        for f in sorted(res_dir.glob("*.png")):
            plan[folder / "resources" / f.name] = f

    v = manifest["vertical"]
    message = message or (f"{name} {map_hash(manifest)}: scale {v['height_scale_m']:.0f} m, "
                          f"sea {v['sea_level_m']:.0f} m, x{v['exaggeration']:.2g}")
    progress(f"[publish] {'would write' if dry_run else 'writing'} {folder.name}/ ({len(plan) + 1} files) -> {repo}")
    if dry_run:
        return Published(folder, None, [p.name for p in plan] + ["README.md"], False)

    if push:
        r = _git(repo, "pull", "--ff-only", check=False)
        if r.returncode != 0:
            progress(f"[publish] warning: git pull failed ({r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'unknown'}); continuing offline")

    folder.mkdir(parents=True, exist_ok=True)
    for dst, src in plan.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    (folder / "README.md").write_text(map_readme(manifest, stem), encoding="utf-8")
    update_index(repo)

    _git(repo, "add", "-A", "--", str(folder.relative_to(repo)), "README.md")
    if not _git(repo, "status", "--porcelain").stdout.strip():
        progress("[publish] nothing changed; already published at this state")
        sha = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
        return Published(folder, sha, [p.name for p in plan] + ["README.md"], False)
    _git(repo, "commit", "-q", "-m", message)
    sha = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    progress(f"[publish] committed {sha}: {message}")
    if lfs:
        _lfs_check(repo, folder, progress)
    pushed = False
    if push:
        _git(repo, "push", "-q")
        pushed = True
        progress(f"[publish] pushed to {_git(repo, 'remote', 'get-url', 'origin', check=False).stdout.strip() or 'origin'}")
    return Published(folder, sha, [p.name for p in plan] + ["README.md"], pushed)
