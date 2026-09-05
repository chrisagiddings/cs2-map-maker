Part of __EPIC__.

## Problem

Channel burning (`src/hydro.py`) and water sources (`src/water_sources.py`) are built on
NHDPlus HR fields: `streamorde`, `flowdir`, `levelpathi`, `hydroseq`, `startflag`,
`totdasqkm`, plus NHDArea/NHDWaterbody polygons. Outside the US there is no equivalent
single source.

## Sources

- **OpenStreetMap via Overpass** (no key, cache like everything else): `waterway=river|stream|canal`
  lines with `name`, `width`, `tunnel`/`culvert`, `intermittent`; `natural=water` polygons with
  `water=river|lake|reservoir|lagoon`; `natural=coastline`.
- **HydroRIVERS** (HydroSHEDS v1, free, global, ~500 m): `ORD_STRA` Strahler order, `UPLAND_SKM`
  upstream area, `MAIN_RIV`, flow direction by segment order, `DIS_AV_CMS` mean discharge.
  Download once per continent (tens of MB) and cache.

## Design

- `HydroSource` interface returning the same three frames the pipeline already uses:
  flowlines (with `streamorde`, `flowdir`, `levelpathi`, `hydroseq`, `startflag`, `totdasqkm`,
  `gnis_name`), areas, waterbodies. NHD implements it as today; `OsmHydroSource` builds it from
  OSM + HydroRIVERS.
- Building the OSM frame:
  - order: nearest HydroRIVERS segment within 300 m -> `ORD_STRA`; else `waterway=river` -> 3,
    `stream` -> 1, `canal` -> 2; record `order_source` per feature.
  - direction: OSM waterways are drawn downstream by convention; sanity-check against the DEM
    (mean elevation of the first vs last third of the line) and flip when it disagrees, set
    `flowdir=0` when the DEM is ambiguous (< 0.5 m difference).
  - `levelpathi`: connected components of same-name ways; `hydroseq`: order along the component.
  - `totdasqkm`: HydroRIVERS `UPLAND_SKM` of the matched segment.
  - polygons: `natural=water` with `water=river` -> areas (ftype 460), lake/reservoir/lagoon ->
    waterbodies (390/436/493), coastline -> a sea polygon (445) clipped to the world extent.
- **Culverts and tunnels**: a way tagged `tunnel=culvert|yes` or `covered=yes` is kept for
  direction/order continuity but excluded from burning and from water-source proposals. Lviv's
  Poltva is the test.
- Intermittent waterways (`intermittent=yes`) burn at half depth and are flagged in the record.
- Overpass etiquette: one query per extent, `[timeout:180]`, 2 s backoff on 429/504, mirrors list.
- Tests: synthetic OSM+HydroRIVERS frames produce the expected NHD-shaped frame; culvert
  exclusion; direction flip against a synthetic DEM.

## Sites this unblocks

Chisinau, Lviv, Uji, Pune, Bogota, Lagos, Turin. Also a useful cross-check on US sites:
run both sources on Chattanooga and diff the water-source proposals.
