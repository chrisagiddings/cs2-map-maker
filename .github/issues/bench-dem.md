Part of __EPIC__.

## Problem

`src/fetch.py` only knows USGS 3DEP, which covers the US. Seven of the ten benchmark sites
are elsewhere.

## Sources

| source | coverage | resolution | kind | access |
|---|---|---|---|---|
| Copernicus GLO-30 | global | 30 m | DSM | OpenTopography `globaldem` API (`demtype=COP30`, free key) or AWS `s3://copernicus-dem-30m` (no key) |
| ALOS AW3D30 | global | 30 m | DSM | OpenTopography (`AW3D30`) |
| NASADEM / SRTMGL1 | 60N-56S | 30 m | DSM-ish | OpenTopography |
| EU-DTM (OpenGeoHub) | Europe | 30 m | DTM | OpenTopography (`EU_DTM`); check Moldova/Ukraine coverage |
| TINITALY | Italy | 10 m | DTM | INGV download, registration |
| GSI Fundamental Geospatial Data | Japan | 5 m / 10 m | DTM | GSI download (registration), JGD2011 |
| USGS 3DEP | US | 1-10 m | DTM | already implemented |

## Design

- `ElevationSource` interface: `covers(site) -> bool`, `fetch(site, bbox, m_per_px) -> (array, transform, meta)`,
  `meta` includes `product`, `native_m`, `kind` (`dtm`/`dsm`), `vertical_datum`.
- A resolver picks the finest DTM that covers the extent; falls back to the finest DSM; records
  the choice in the manifest (`dem_source.kind`, `dem_source.product`) and on the QA sheet.
- Playable and world may come from different sources (e.g. GSI 5 m playable, COP30 world).
  The seam metric already measures the mismatch; #__DSM__ corrects the offset.
- **Resampling policy by native resolution**, recorded in the manifest:
  - native <= 1.75 m: fetch at 1.75 m, block-average to 3.5 m (current behaviour)
  - 1.75-3.5 m: fetch at native, bilinear to 3.5 m
  - > 3.5 m (10 m, 30 m): fetch at native, **cubic-spline upsample** to 3.5 m, then the
    de-terrace filter only if terracing is detected. Never add synthetic noise or "detail";
    the QA sheet states the true source resolution so nobody mistakes it for LiDAR.
- Vertical datum: COP30 is EGM2008, 3DEP is NAVD88, GSI is JGD2011 orthometric. All
  orthometric, so within-site consistency holds; record the datum anyway.
- Cache and tiling as today; OpenTopography has a 500 MB / request cap and rate limits,
  so tile at <= 4000 px and back off on 429.
- Tests: resolver picks 3DEP for Chattanooga, COP30 for Lagos; resampling policy branches;
  an OpenTopography fetch test that skips without a key (`OPENTOPO_KEY`).

## Sites this unblocks

Chisinau, Lviv, Uji, Pune, Bogota, Lagos, Turin.
