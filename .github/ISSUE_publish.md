## Goal

Add a `publish` command that commits a finished map into the companion repo
[`cs2-map-maker-maps`](https://github.com/chrisgiddings/cs2-map-maker-maps) so every
map the pipeline produces is versioned, browsable, and shareable without re-running the pipeline.

```
python make_map.py --center 35.0456,-85.3097 --name chattanooga
python publish_map.py --name chattanooga            # -> commits out/chattanooga to the maps repo
python make_map.py ... --publish                     # or: build and publish in one go
```

## What gets committed

Everything needed to use the map in CS2, plus the QA material:

```
Chattanooga - a4f662m/
├── README.md                      # generated: height scale, sea level, centre, how to import
├── Chattanooga_heightmap.png      # 4096² 16-bit playable heightmap
├── Chattanooga_worldmap.png       # 4096² 16-bit world map
├── Chattanooga_qa.png             # QA contact sheet
├── Chattanooga_manifest.json      # full run record (bbox, CRS, height scale, sources, stats)
└── resources/                     # once Stage 6 lands: ore.png, oil.png, fertile.png, water.png (256²)
```

## Folder naming: `{City name} - {short-hash}`

Each map gets its own folder named `{name} - {hash}`, e.g. `Chattanooga - a4f662m`.
The hash is derived from the **inputs that define the map**, not from the file bytes, so
re-publishing an identical configuration lands in the same folder (an update), while any
change that alters the terrain creates a new folder:

- centre lat/lon (rounded to 1e-4), EPSG
- exaggeration, sea level, water-surface override
- burn on/off, min stream order, depth scale, deterrace mode, oversample
- pipeline version (git commit of `cs2-map-maker`) - optional, see open question

`hash = sha1(canonical JSON of the above)[:7]`. The manifest already records every one of
these under `params` and `run`, so the hash can be computed from the manifest alone.

The `name` part is taken from `--name` with the first letter capitalised and underscores
turned into spaces; the hash makes collisions between "Chattanooga", "chattanooga" and
"Chattanooga_v2" impossible.

## Implementation sketch

1. `src/publish.py`
   - `map_hash(manifest) -> str`
   - `folder_name(name, manifest) -> str`
   - `publish(out_dir, maps_repo_path, *, message=None, push=True)`:
     clone or pull the maps repo (default `../cs2-map-maker-maps`, overridable with
     `--maps-repo` / `CS2_MAPS_REPO`), copy the files, write the per-map `README.md`,
     regenerate the top-level `README.md` index (table: folder, centre, height scale, buildable %,
     QA thumbnail), `git add`, commit with a message like
     `chattanooga a4f662m: scale 610 m, sea 63 m, x1.0`, push.
2. `publish_map.py` thin CLI; `--publish` flag on `make_map.py` that calls it after the QA sheet.
3. Refuse to publish if the manifest's `outputs` block is missing or the heightmap fails
   `verify_heightmap` - never commit a bad PNG.
4. Tests: hash stability (same params -> same hash; any param change -> different hash),
   folder naming, README index generation on a temp repo.

## Repo size

Each map is ~50-70 MB of 16-bit PNGs. GitHub's soft limit is 1 GB per repo, so the maps repo
holds ~15 maps before it gets awkward. Options, in order of preference:

- **Git LFS for `*.png`** in the maps repo (`git lfs track "*.png"`). Free tier is 1 GB storage /
  1 GB bandwidth per month; fine for a personal collection, and the repo stays fast to clone.
- Keep only the heightmap + worldmap under LFS and commit QA sheets / manifests as normal files
  so the browsable part stays cheap.
- Publish a GitHub Release per map with the PNGs as assets, committing only manifest + QA sheet.

## Open questions

- Should the pipeline version be part of the hash? Including it means a code change
  re-publishes every map into new folders; excluding it means an improved burn silently
  overwrites the old map. Suggest: exclude from the hash, but record it in the manifest and
  write it into the folder README so the history is visible via git log.
- Naming when `--name` is an arbitrary slug rather than a city: fall back to reverse-geocoding
  the centre (Nominatim, no key) to get a city name, with `--display-name` to override.

## Further improvements worth their own issues

- **Stage 6 resource masks** (NLCD, gSSURGO, MRDS) - already planned.
- **Map gallery**: a static page generated from the maps repo index (GitHub Pages) showing each
  QA sheet with its numbers, so maps can be picked visually.
- **`--water-source-hints`**: emit the edge pixels where order >= 6 flowlines enter/leave the
  playable area, with their in-game elevations, so river water sources can be placed in the
  editor without guesswork.
- **Coastal support**: NOAA CUDEM bathymetry merge when the world extent touches the sea.
- **Re-run detection**: `make_map.py` warns when an identical configuration is already in the
  maps repo and offers to skip.
