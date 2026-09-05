## Why

Everything so far has been tuned on one site. Chattanooga has 0.8 m LiDAR, a clean NHD network,
an order-9 river with a polygon, 485 m of relief and no coast. Most of the world has none of
that. To stop over-indexing, the pipeline gets a fixed **benchmark set** of ten sites chosen for
different terrain, data quality and hydrology, a scorecard per site, and explicit compensation
steps where the sources differ.

## Principle

Use the **best available source for each location**, but do not let North America set the
defaults. Seven of the ten sites are outside the US and that is the point: every threshold,
filter and heuristic in the pipeline (terracing detection, channel width/depth by order, the
reference water surface pick, height-scale rounding, buildability) is judged on the whole set,
and a change that improves Chattanooga while degrading Pune or Lagos is a regression. Concretely:

- The harness reports the non-US sites first and the definition of done requires all ten, not
  "the US ones plus best effort".
- Nothing in `src/` may assume 3DEP/NHD field names or resolutions; the source interfaces in
  #__DEM__ and #__HYDRO__ are the only place source specifics live.
- Expected ranges are set per site from a visual review of that site, never copied from
  Chattanooga.

## Benchmark sites

| slug | site | centre | UTM | what it stresses |
|---|---|---|---|---|
| `chattanooga` | Chattanooga, TN, US | 35.0456, -85.3097 | 32616 | baseline: LiDAR, big meandering river, 485 m relief |
| `park_city` | Park City, UT, US | 40.6461, -111.4980 | 32612 | high altitude (2,100 m), ~1,000 m playable relief, no large river, ski terrain, height scale near 2,000 m |
| `cheyenne` | Cheyenne, WY, US | 41.1400, -104.8202 | 32613 | very flat high plains, tiny creeks only, height scale at the 200 m floor, exaggeration matters |
| `chisinau` | Chisinau, Moldova | 47.0105, 28.8638 | 32635 | rolling hills, small river (Bic), **30 m global DEM only** -> 8.6x upsampling |
| `lviv` | Lviv, Ukraine | 49.8397, 24.0297 | 32635 | sits on the European watershed; main river (Poltva) is **culverted underground**; world extent crosses the 24 E UTM zone boundary |
| `uji` | Uji, Japan | 34.8845, 135.7997 | 32653 | dense urban + steep forested hills, Uji River with weirs, national 5-10 m DEM (GSI) in JGD2011 |
| `pune` | Pune, India | 18.5204, 73.8567 | 32643 | Deccan plateau, Mula/Mutha rivers with dams upstream, monsoon regime, DSM building artefacts |
| `bogota` | Bogota, Colombia | 4.7110, -74.0721 | 32618 | 2,600 m plateau against a 3,600 m cordillera, near-equatorial, flat wetlands (humedales) |
| `lagos` | Lagos, Nigeria | 6.5244, 3.3792 | 32631 | **coastal** lagoon + Atlantic, near sea level, needs bathymetry (#6), DSM with dense buildings |
| `turin` | Turin, Italy | 45.0703, 7.6869 | 32632 | flat city at the Po / Dora Riparia confluence, Alpine foothills in the world extent, TINITALY 10 m |

Add them to `bench/sites.yaml`; `--site <slug>` on `make_map.py` resolves a slug to its centre.

## Validation points (the per-site scorecard)

Every benchmark run writes `bench/results/<slug>.json` and one row in `bench/results.md`
with the QA thumbnail. Each metric has an expected range per site (recorded once, reviewed by
eye) so a later run can be checked against it:

| metric | source | why it matters |
|---|---|---|
| source DEM resolution + product name | manifest `dem_source` | 0.8 m vs 30 m changes every downstream choice |
| DEM kind: DTM or DSM | new manifest field | DSMs carry buildings/trees; must be cleaned (see #__DSM__) |
| nodata fraction filled | `nodata_filled_fraction` | voids in SRTM-family data on steep slopes |
| terracing fraction before/after | `terracing` | integer-metre sources; verify de-terrace triggers |
| playable + world relief, height scale, m per level | `playable`, `vertical` | Park City ~2,000 m scale vs Cheyenne 200 m floor |
| buildable % of land | `playable` | Cheyenne ~95 %, Park City < 30 % expected |
| water fraction, reference water surface + how it was chosen | `water_fraction`, `vertical.water_surface_source` | sites with no big river must not pivot on a farm pond |
| number and kind of water sources proposed | `water_sources` | Lviv should propose none for the culverted Poltva |
| world-centre seam before overwrite (m) | `world_center_mean_abs_diff_before_replace_m` | mixed sources (5 m national playable vs 30 m global world) show up here |
| slope histogram of the playable at 3.5 m | new | upsampled 30 m data has a tell-tale smooth histogram; a proxy for "invented detail" |
| wall-clock and bytes downloaded | new | keeps the benchmark runnable |

**Drift** is checked two ways:
1. **Across sites** (consistency): the same kind of terrain should score similarly regardless
   of source. E.g. a flat plain must not get a lower buildable % just because its source is
   30 m and noisy.
2. **Across pipeline versions** (regression): `tools/bench.py --compare <previous results>`
   flags any site whose buildable % moves > 2 points, height scale changes, water-source
   count changes, or seam metric doubles. Store results per git commit so the history is
   visible.

## Compensation / normalisation needed (each a sub-issue)

- [ ] #__DEM__ global elevation: Copernicus GLO-30 (via OpenTopography API or AWS) as the
  fallback everywhere 3DEP is absent, national higher-res sources where they exist
  (GSI Japan, TINITALY, EU-DTM), one interface, resolution-aware resampling policy.
- [ ] #__HYDRO__ global hydrography: OSM waterways + water polygons via Overpass, stream order
  and direction from HydroRIVERS, culvert/tunnel handling, one interface with NHD.
- [ ] #__DSM__ DSM-to-terrain cleaning for urban sites (Lagos, Pune, Bogota, Uji when only a
  DSM is available), and mixed-source vertical offset correction between playable and world.
- [ ] #__BENCH__ benchmark harness: `bench/sites.yaml`, `tools/bench.py`, scorecard, results
  table with thumbnails, comparison mode, and the expected ranges for all ten sites.

## Definition of done

- All ten sites build end to end with `make_map.py --site <slug>` and produce heightmaps,
  QA and guide sheets; none silently produce a flat or empty map.
- `bench/results.md` shows all ten with their scorecards, and the per-site expected ranges
  are committed.
- The three known hard cases have explicit, tested handling: Lagos (coast, DSM), Lviv
  (culverted river, zone boundary), Cheyenne (flat, no big river, height-scale floor).
- Any source-specific constant lives in config keyed by source, not in code.
