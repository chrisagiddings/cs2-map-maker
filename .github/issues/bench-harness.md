Part of __EPIC__.

## Deliverables

- `bench/sites.yaml`: the ten benchmark sites (slug, display name, centre, notes, tags such as
  `coastal`, `flat`, `high-relief`, `dsm-only`, `culverted`), plus the expected ranges once
  reviewed.
- `make_map.py --site <slug>` resolves a slug to its centre and display name (also feeds #3).
- `tools/bench.py`:
  - `run [--only slug,...] [--exaggeration ...]` builds each site, collects the scorecard from the
    manifest into `bench/results/<slug>.json` (keyed by git commit and date), and rewrites
    `bench/results.md`: one row per site with QA + guide thumbnails (600 px JPEGs committed under
    `bench/thumbs/`), source, resolution, kind, relief, height scale, buildable %, water %,
    water sources, seam, terracing, elapsed, bytes.
  - `compare <old.json dir>`: prints a table of metric deltas and exits non-zero when a site
    drifts outside its expected range or beyond the regression thresholds (buildable +-2 pts,
    any height-scale change, water-source count change, seam x2).
  - `check`: the same comparison against the committed expected ranges, meant for CI later.
- Scorecard additions to the manifest: slope histogram (10 bins), DEM kind, bytes downloaded,
  wall-clock per stage.
- Expected ranges for all ten sites, filled in after the first full run and a visual review of
  every QA sheet. They are a judgement, not a measurement, so they live in `sites.yaml` with a
  comment per site saying why.

## Notes

- The full set downloads several GB on first run; the cache makes reruns cheap. `--only` keeps
  iteration fast.
- Publish the benchmark maps to `cs2-map-maker-maps` as well (they are real maps), tagged
  `benchmark` in the index.
- Depends on #__DEM__ and #__HYDRO__ for the non-US sites; the three US sites can run first
  and establish the harness.
