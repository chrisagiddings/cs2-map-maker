Part of __EPIC__.

## Problem 1: DSM artefacts

Copernicus GLO-30 and AW3D30 are surface models. In Lagos, Pune, Bogota and central Uji the
"terrain" includes 10-40 m of buildings and tree canopy. Imported as-is, a city centre becomes
a plateau of lumps, slope is wrong everywhere, and river banks get walls where trees line them.

## Proposal

- Manifest and QA state `dem_source.kind = dsm` whenever the chosen source is one.
- `terrain.dsm_to_dtm(dem, m_per_px, urban_mask)`:
  - morphological opening (grey erosion then dilation) with a window sized to the largest
    building footprint (~60-100 m), applied where an urban/forest mask says so;
  - mask from OSM `landuse=residential|commercial|industrial` and `natural=wood` polygons
    (same Overpass fetch as #__HYDRO__), buffered 30 m;
  - outside the mask, a light 3x3 median only; ridgelines stay untouched (reuse the local-range
    guard from `deterrace`).
  - report the fraction of pixels changed and the mean height removed on the QA sheet.
- Never apply it to a DTM source; the flag makes that explicit.
- Tests: a synthetic ramp with 25 m boxes on it comes back within 1 m of the ramp inside the
  mask and untouched outside; a synthetic ridge survives.

## Problem 2: mixed-source vertical offset

When the playable area comes from a national DTM and the world map from GLO-30, the two
disagree by a constant-ish offset (datum, epoch, DSM bias) and the world-centre overwrite hides
a step at the playable border. The seam metric already measures it.

## Proposal

- Compute the offset in a 500 m ring just outside the playable footprint: median of
  (world - playable_downsampled) after both are on the world grid.
- If |offset| > 1 m, shift the **world** map by it (the playable is the higher-quality source),
  feather the residual over the ring with a linear ramp, and record `world_offset_applied_m`.
- QA sheet prints seam before and after; the benchmark expects seam-after < 0.5 m everywhere.
- Tests: synthetic playable/world pair with a 3 m offset -> corrected to < 0.2 m; no change
  when the offset is < 1 m.
