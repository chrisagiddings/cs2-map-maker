## Problem

3DEP has no bathymetry. A coastal or estuary site imports as a flat plate at sea level and CS2's
sea-level water sits on it with no depth.

## Proposal

- Detect when NHDArea `SeaOcean` / `BayInlet` / `Estuary` polygons intersect the world extent.
- Fetch NOAA CUDEM (1/9 or 1/3 arc-second, NAVD88) for the extent and merge: bathymetry
  below the coastline polygon, 3DEP above, feathered over ~50 m.
- GEBCO 2024 as a coarse fallback outside CUDEM coverage.
- The reference water surface becomes 0 m NAVD88 (or the local MHW if desired); the sea-level
  logic in `src/normalize.py` already handles it once the DEM has real depths.
- Add a coastal test site (e.g. Charleston SC) to the README examples.
