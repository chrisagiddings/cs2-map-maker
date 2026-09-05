# cs2-maps

Turn real-world USGS elevation and hydrography into import-ready
**Cities: Skylines II** heightmaps.

```powershell
.\.venv\Scripts\python.exe make_map.py --center 35.0456,-85.3097 --name chattanooga
.\.venv\Scripts\python.exe make_map.py --bbox -85.4,35.0,-85.2,35.1 --name foo --sea-level 200 --exaggeration 1.3
```

Outputs land in `out/<name>/`:

| File | What it is |
|---|---|
| `<name>_heightmap.png` | 4096² 16-bit playable heightmap (14.336 km, 3.5 m/px) |
| `<name>_worldmap.png` | 4096² 16-bit world map (57.344 km, 14 m/px); centre 1024² matches the heightmap |
| `<name>_qa.png` | contact sheet: hillshades, slope classes, cross-sections, numbers |
| `<name>_manifest.json` | everything about the run: bbox, CRS, height scale, sea level, sources, stats |

**Importing:** copy both PNGs to
`%USERPROFILE%\AppData\LocalLow\Colossal Order\Cities Skylines II\Heightmaps\`,
import them in the editor, and set the **height scale** to the number printed
in the console banner (also in the QA sheet and manifest).

## Options that matter

| Flag | Effect |
|---|---|
| `--exaggeration 1.3` | vertical exaggeration about the water surface (river stays put) |
| `--sea-level 80` | in-game elevation of the reference water surface; default is the lowest feasible |
| `--water-surface 193.3` | override the detected real-world water surface if the auto-pick is wrong |
| `--no-burn` | skip channel burning, for comparison |
| `--depth-scale 1.5` | deeper/shallower channels |
| `--oversample 1` | fetch at 3.5 m directly instead of 1.75 m + average (faster, coarser) |

## How it works

1. Site centre → local UTM zone → exact 14.336 km and 57.344 km footprints (`src/geo.py`).
   `--site <slug>` picks one of the benchmark sites in `bench/sites.json`.
2. Elevation comes from the finest terrain model covering the site (`src/elevation.py`):
   USGS 3DEP in the US (1 m LiDAR where it exists), your own national DTMs dropped into
   `data/local/`, or Copernicus GLO-30 anywhere on Earth. Everything is warped **in UTM at the
   target pixel size** (never resampled in degrees) and cached under `data/raw/`.
3. Integer-metre sources are de-terraced with an edge-preserving filter (`src/terrain.py`).
4. Rivers come from NHDPlus HR in the US and from OpenStreetMap + HydroRIVERS elsewhere
   (`src/hydro_sources.py`); polygons and flowlines carve beds below the DEM's water surface,
   sized by stream order or by discharge where it is known (`src/hydro.py`).
5. One vertical transform for both maps: reference water surface, sea level,
   exaggeration, one height scale (`src/normalize.py`).
6. The world map centre is overwritten with the downsampled playable map, both are
   written as 16-bit PNGs and read back with an independent library (`src/export.py`).

Tests: `.\.venv\Scripts\python.exe -m pytest -q`. See `CLAUDE.md` for the spec and decisions.
