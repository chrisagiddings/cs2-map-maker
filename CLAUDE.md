# cs2-maps — real-world terrain → Cities: Skylines II heightmaps

Reusable Python pipeline: USGS/GIS data → import-ready CS2 heightmaps.
Goal is maps that look convincing to CS2 players, not a one-off script.

## Environment
- Windows 11, no admin. Python via **uv** (`C:\Users\Client\.local\bin\uv.exe`).
- Venv at `.venv` (Python 3.12.14). Run things with `.venv\Scripts\python.exe` or `uv run`.
- Installed: rasterio 1.5 (GDAL 3.12 bundled, 193 drivers incl. GTiff/PNG/GeoJSON),
  numpy, scipy, pyproj, geopandas, shapely, requests, pillow, matplotlib, imageio.
- `tools\verify_env.py` prints versions and runs a 16-bit PNG round-trip check.
- Git for Windows 2.55 is installed.

## Working agreement
- Work in **stages**; stop after each and show output. Don't run end-to-end unasked.
- Don't invent data. If a source returns nothing for a bbox, raise a real error and stop.
- Location-agnostic: anything that varies by site is a CLI flag or config value.
- If something in this spec turns out to be wrong, say so and update this file.
- Cache every download to `data/raw/` keyed by bbox hash. Re-runs must not re-download.

## CS2 hard spec (contract — CS2 silently rejects/mangles anything else)
| Thing | Value |
|---|---|
| Heightmap resolution | **4096 × 4096** exactly |
| Bit depth / format | **16-bit grayscale** PNG or TIFF (single channel; CS2 reads only red if RGB) |
| Playable area | **14.336 km × 14.336 km** → **3.5 m / px** |
| World map | same 4096×4096, covers **57.344 km** → **14 m / px** |
| World map constraint | its **centre 1024×1024 must equal** the playable heightmap downsampled 4× |
| Height mapping | px 0 = 0 m, px 65535 = height scale set in-editor |
| Height scale range | **200 m – 10,000 m**, default 4000 m |
| Resource maps | separate **256 × 256** grayscale, white = deposit present |

Trap: Pillow's `I;16` PNG writing is quirky. Every export must be read back with an
independent reader and asserted: shape (4096, 4096), dtype uint16, value range spans data.
(Verified: rasterio/GDAL write → Pillow read round-trips uint16 correctly.)

## Data sources (free)
- **Elevation (primary):** USGS 3DEP ImageServer, no key —
  `https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage`
  (bbox, size, `format=tiff`, in/out SR). Prefer 1 m LiDAR, fall back to 1/3 arc-sec (~10 m).
- **Elevation (alt):** OpenTopography API (free key).
- **Hydrography:** NHDPlus HR MapServer, no key —
  `https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer` (flowlines + waterbodies, need stream order).
- **Bathymetry (coastal):** NOAA CUDEM; GEBCO coarse fallback.
- **Land cover:** NLCD (MRLC). **Soils:** gSSURGO / Soil Data Access → fertile land.
- **Minerals:** USGS MRDS + State Geologic Map Compilation → ore / oil.

## Pipeline correctness rules (acceptance criteria)
1. **Reproject before resampling.** Local UTM (metric CRS), then resample to exactly 3.5 m/px.
   Resampling in WGS84 degrees = horizontally stretched map.
2. **One normalization for both heightmaps.** min/max over the union of playable + world
   extents; one height scale for both. Otherwise a cliff at the playable border.
3. **Burn river channels.** DEMs record the water surface. Carve channels from NHD flowlines,
   width/depth keyed to stream order. Highest-impact step.
4. **Decide sea level explicitly.** Choose water-surface elevation, offset DEM so it lands there, report it.
Also: de-terrace integer-metre DEMs with dithering or bilateral filter (never global Gaussian);
vertical exaggeration parameter (default 1.0, try 1.2–1.5); report % playable area under 10% slope
(below ~40% buildable is frustrating).

## QA loop
Every run emits one contact-sheet PNG (≤ ~2000 px): playable hillshade, world hillshade with
playable footprint outlined, slope/buildability heatmap, mid-map cross-section, text panel
(min/max elev, relief, **exact height scale for the editor**, water surface elev, % buildable,
source DEM resolution). Print the height scale prominently in the console.

## Tool shape
```
python make_map.py --center 35.0456,-85.3097 --name chattanooga --exaggeration 1.0
python make_map.py --bbox <minx,miny,maxx,maxy> --name foo --sea-level 200
```
Outputs `out/<name>/`: `<name>_heightmap.png`, `<name>_worldmap.png`, `<name>_qa.png`,
`<name>_manifest.json` (bbox, CRS, height scale, sea level, source URLs, stats), resource masks (256²).
Layout: `src/` modules (fetch, reproject, hydro, normalize, export, qa); thin `make_map.py` CLI;
`data/raw/` and `out/` gitignored.

## First target
Chattanooga, TN — centre 35.0456, -85.3097 (Lookout Mountain ~500 m relief vs Tennessee River,
good LiDAR, big meandering river to prove channel burning).

## Stage log
- Stage 0 (2026-09-05): env bootstrapped, versions verified, CLAUDE.md written. DONE.
- Stage 1: spec (above) — confirm against CS2 wiki/dev diary; write export + readback test.
- Stage 2: fetchers + cache. Stage 3: pipeline rules. Stage 4: QA sheet. Stage 5: CLI. Stage 6: resources.
