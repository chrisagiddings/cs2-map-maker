## Goal

Add a `publish` command that commits a finished map into the companion repo
[`cs2-map-maker-maps`](https://github.com/chrisagiddings/cs2-map-maker-maps) so every
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
└── resources/                     # once #8 lands: ore.png, oil.png, fertile.png, water.png (256²)
```

## Folder naming: `{City name} - {short-hash}`

Each map gets its own folder named `{name} - {hash}`, e.g. `Chattanooga - a4f662m`.
The hash is derived from the **inputs that define the map**, not from the file bytes, so
re-publishing an identical configuration lands in the same folder (an update), while any
change that alters the terrain creates a new folder:

- centre lat/lon (rounded to 1e-4), EPSG
- exaggeration, sea level, water-surface override
- burn on/off, min stream order, depth scale, deterrace mode, oversample

`hash = sha1(canonical JSON of the above)[:7]`. The manifest already records every one of
these under `params` and `site`, so the hash is computed from the manifest alone.

The name part is taken from `--name` with underscores turned into spaces and words
capitalised, until #3 adds a proper display name. The hash makes collisions between
"Chattanooga", "chattanooga" and "Chattanooga_v2" impossible.

## Decisions

- **The pipeline's git commit is not part of the hash.** Including it would move every map
  into a new folder on each code change. Instead it is recorded in the manifest and the
  per-map README, so `git log` on a folder shows how the map evolved as the pipeline improved.
- **Display name** comes from the slug for now; reverse-geocoding is #3.

## Implementation

1. `src/publish.py`
   - `map_hash(manifest) -> str`
   - `folder_name(manifest) -> str`
   - `publish(out_dir, maps_repo, *, message=None, push=True, dry_run=False)`:
     verify the maps repo is a git checkout with a clean tree, copy the files, write the
     per-map `README.md`, regenerate the index table in the top-level `README.md` from all
     folders' manifests, `git add`, commit with a message like
     `chattanooga a4f662m: scale 610 m, sea 63 m, x1.0`, push.
2. `publish_map.py` thin CLI; `--publish` flag on `make_map.py` that calls it after the QA sheet.
   Maps repo location: `--maps-repo`, else `CS2_MAPS_REPO`, else `../cs2-map-maker-maps`.
3. Refuse to publish if the manifest's `outputs` block is missing or the heightmap fails
   `verify_heightmap`. Never commit a bad PNG.
4. Tests: hash stability (same params -> same hash; any listed param change -> different
   hash; unrelated changes such as `--out` do not change it), folder naming, index generation
   on a temp repo, refusal on a bad heightmap.

## Related

- #2 Git LFS for the PNGs in the maps repo (publish should warn when LFS is not set up)
- #3 display name via `--display-name` / reverse geocode
- #4 GitHub Pages gallery generated from the maps repo
- #7 detect an already-published configuration before building
- #8 resource masks land in `resources/` and are copied by publish
