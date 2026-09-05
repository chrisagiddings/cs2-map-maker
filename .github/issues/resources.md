## Stage 6 of the project brief: resource masks

CS2 imports 256×256 grayscale masks (white = deposit present) for ore, oil, fertile land and
underground water. Generate them from public data for the playable extent:

- **Fertile land**: gSSURGO / Soil Data Access — map units with high `nccpi` (National
  Commodity Crop Productivity Index) or farmland class `Prime farmland`.
- **Ore**: USGS MRDS occurrences (metallic commodities) buffered ~500 m; optionally the State
  Geologic Map Compilation lithology for where deposits are plausible.
- **Oil**: MRDS / state oil & gas well points buffered; treat absence honestly (many areas
  have none — write an all-black mask and say so).
- **Underground water**: NLCD wetlands + low-slope valley floor near order >= 4 streams as a
  proxy, or skip if too speculative.
- Land cover (NLCD) is also the input for a future terrain-material paint; fetch and cache it
  now even if only fertile land uses it.

Deliverables: `out/<name>/resources/{ore,oil,fertile,water}.png` via the existing
`write_resource_mask`, a resource row on the QA sheet, and `publish` (#1) copies the folder.
