## Problem

CS2 rivers need water sources placed by hand in the editor. After importing a burned heightmap
the user has to guess where each river enters and leaves the playable area and at what height.

## Proposal

Emit `<name>_water_sources.json` (and list them on the QA sheet) with one entry per flowline of
order >= 6 that crosses the playable boundary:

```json
{"name": "Tennessee River", "order": 9, "edge": "N", "px": [2811, 0],
 "in_game_xy_m": [9838.5, 0.0], "surface_m": 63.0, "bed_m": 51.0, "direction": "inflow"}
```

- Direction from NHD `flowdir` / `fromnode`→`tonode` so inflow vs outflow is explicit.
- Also emit the centre of each water polygon > 1 km² that lies wholly inside the playable
  area (lakes need a source too).
- Draw the points on the QA playable hillshade with arrows.
