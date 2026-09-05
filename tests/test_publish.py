import copy
import json
import subprocess

import numpy as np
import pytest

from src.export import write_heightmap
from src.publish import (map_hash, folder_name, display_name, hash_inputs, publish,
                         update_index, PublishError, INDEX_START, INDEX_END)

N = 4096


def base_manifest(name="chattanooga", **over):
    m = {
        "site": {"name": name, "lat": 35.0456, "lon": -85.3097, "epsg": 32616},
        "params": {"exaggeration": 1.0, "sea_level_m": None, "water_surface_real_m": None,
                   "oversample": "auto", "deterrace": "auto", "burn": True,
                   "hydro": {"min_order": 2, "depth_scale": 1.0}, "floor_margin_m": 5.0},
        "vertical": {"height_scale_m": 610.0, "sea_level_m": 63.0, "water_surface_real_m": 193.3, "exaggeration": 1.0},
        "playable": {"min_real_m": 172.0, "max_real_m": 656.0, "relief_m": 484.0, "buildable_fraction_land": 0.506},
        "water_fraction": {"playable": 0.062},
        "dem_source": {"finest_ground_m": 0.82, "finest_name": "GA"},
        "water_polygons_playable": [{"name": "Tennessee River"}],
        "run": {"argv": ["--center", "35.0456,-85.3097", "--name", name], "generated": "2026-09-05T12:00:00", "git_commit": "abc1234"},
        "outputs": {"heightmap": {}, "worldmap": {}},
    }
    for k, v in over.items():
        d = m
        *path, last = k.split(".")
        for p in path:
            d = d[p]
        d[last] = v
    return m


def test_hash_is_stable_and_input_sensitive():
    m = base_manifest()
    h = map_hash(m)
    assert len(h) == 7 and h == map_hash(copy.deepcopy(m))
    # unrelated fields must not change it
    m2 = base_manifest(**{"run.git_commit": "zzz9999", "vertical.height_scale_m": 999.0})
    assert map_hash(m2) == h
    # every defining input must
    for change in ({"params.exaggeration": 1.3}, {"params.sea_level_m": 200.0}, {"params.burn": False},
                   {"params.hydro.min_order": 3}, {"params.hydro.depth_scale": 1.5}, {"params.deterrace": "on"},
                   {"params.oversample": "1"}, {"site.lat": 35.0457}, {"site.epsg": 26916},
                   {"params.water_surface_real_m": 190.0}):
        assert map_hash(base_manifest(**change)) != h, change
    # centre rounding: 1e-5 wobble is the same map
    assert map_hash(base_manifest(**{"site.lat": 35.04561})) == h
    assert set(hash_inputs(m)) == {"lat", "lon", "epsg", "exaggeration", "sea_level_m", "water_surface_real_m",
                                   "burn", "min_order", "depth_scale", "deterrace", "oversample"}


def test_naming():
    assert display_name(base_manifest("chattanooga")) == "Chattanooga"
    assert display_name(base_manifest("new_york_city")) == "New York City"
    assert display_name(base_manifest("x", display_name="São Paulo")) == "São Paulo"
    fn = folder_name(base_manifest())
    assert fn.startswith("Chattanooga - ") and len(fn) == len("Chattanooga - ") + 7
    # same config, different slug -> same hash, different folder
    assert map_hash(base_manifest("chatt_v2")) == map_hash(base_manifest("chattanooga"))
    assert folder_name(base_manifest("chatt_v2")) != folder_name(base_manifest("chattanooga"))


@pytest.fixture
def ramp():
    y, x = np.mgrid[0:N, 0:N]
    a = ((x + y) / (2 * (N - 1)) * 65535).astype(np.uint16)
    a[0, 0], a[-1, -1] = 0, 65535
    return a


def make_out(tmp_path, ramp, name="chattanooga", manifest=None):
    out = tmp_path / "out" / name
    out.mkdir(parents=True)
    write_heightmap(out / f"{name}_heightmap.png", ramp, verify=False)
    write_heightmap(out / f"{name}_worldmap.png", ramp, verify=False)
    (out / f"{name}_qa.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (out / f"{name}_manifest.json").write_text(json.dumps(manifest or base_manifest(name)))
    return out


def make_repo(tmp_path):
    repo = tmp_path / "maps"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("# maps\n\nintro\n\n## Maps\n\n| old | table |\n|---|---|\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(k, "Test")
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, "test@example.com")


def test_publish_end_to_end(tmp_path, ramp):
    out = make_out(tmp_path, ramp)
    repo = make_repo(tmp_path)
    log = []
    r = publish(out, repo, push=False, progress=log.append)
    assert r.folder.name == folder_name(base_manifest()) and r.commit and not r.pushed
    assert (r.folder / "Chattanooga_heightmap.png").exists()
    assert (r.folder / "Chattanooga_manifest.json").exists()
    readme = (r.folder / "README.md").read_text(encoding="utf-8")
    assert "**610 m**" in readme and "abc1234" in readme
    index = (repo / "README.md").read_text()
    assert INDEX_START in index and INDEX_END in index and "intro" in index
    assert r.folder.name in index and "**610 m**" in index and "| old | table |" not in index
    assert any("LFS" in l for l in log)
    msg = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=repo, text=True, capture_output=True).stdout
    assert msg.startswith(f"chattanooga {map_hash(base_manifest())}: scale 610 m, sea 63 m, x1")
    # re-publish identical -> same folder, no new commit
    r2 = publish(out, repo, push=False, progress=log.append)
    assert r2.folder == r.folder and r2.commit == r.commit
    # a terrain-changing tweak -> second folder, index has two rows
    m = base_manifest(**{"params.exaggeration": 1.3, "vertical.exaggeration": 1.3, "vertical.height_scale_m": 790.0})
    out2 = make_out(tmp_path / "b", ramp, manifest=m)
    r3 = publish(out2, repo, push=False, progress=log.append)
    assert r3.folder != r.folder and r3.folder.parent == repo
    index = (repo / "README.md").read_text()
    assert index.count("| [Chattanooga - ") == 2


def test_publish_refuses_bad_inputs(tmp_path, ramp):
    repo = make_repo(tmp_path)
    out = make_out(tmp_path, ramp)
    (out / "chattanooga_heightmap.png").write_bytes((out / "chattanooga_qa.png").read_bytes())
    with pytest.raises(PublishError, match="refusing"):
        publish(out, repo, push=False, progress=lambda *_: None)
    out2 = make_out(tmp_path / "c", ramp, manifest={k: v for k, v in base_manifest().items() if k != "outputs"})
    with pytest.raises(PublishError, match="outputs"):
        publish(out2, repo, push=False, progress=lambda *_: None)
    with pytest.raises(PublishError, match="not a git checkout"):
        publish(make_out(tmp_path / "d", ramp), tmp_path / "nowhere", push=False, progress=lambda *_: None)
    (repo / "README.md").write_text("dirty")
    with pytest.raises(PublishError, match="uncommitted"):
        publish(make_out(tmp_path / "e", ramp), repo, push=False, progress=lambda *_: None)


def test_dry_run_writes_nothing(tmp_path, ramp):
    out = make_out(tmp_path, ramp)
    repo = make_repo(tmp_path)
    r = publish(out, repo, push=False, dry_run=True, progress=lambda *_: None)
    assert r.commit is None and not r.folder.exists()
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, text=True, capture_output=True).stdout == ""
