Part of __EPIC__.

## Goal

A second contact-sheet image, `<name>_guide.png`, that the other parts of the epic draw onto.
One image, one symbol vocabulary, one legend. Nothing in this issue is about *what* to place;
it is the canvas, the symbol set, and the placement-data contract.

## Deliverables

- `src/guide.py`: `GuideCanvas` that renders the playable hillshade (water tinted, as on the
  QA sheet) at ~1800 px with a 1 km grid and edge labels N/E/S/W, plus a legend panel and a
  numbered callout table on the right (symbol, number, what, in-game x/y in metres, elevation,
  one-line why).
- A **placement record** format that every producer emits and the canvas consumes:
  ```json
  {"kind": "water.border_river", "px": [2811, 0], "xy_m": [9838.5, 0.0], "elev_m": 63.0,
   "label": "Tennessee River inflow", "why": "order-9 flowline enters at N edge", "params": {"flow": "in"}}
  ```
  written to `<name>_placements.json`, one list, drawn in a deterministic order.
- Symbol set (matplotlib markers/paths, no external images): water sources by type (border
  river in/out, border sea, stream source, constant-level lake), resource deposits by type (ore,
  oil, fertile, water), highway/rail/ship/air outside connections, power and water/sewer utility
  connections. Distinct shape **and** colour per kind; legend always drawn; colour-blind safe
  (validate the categorical set).
- Callout numbers on the image match the table; the table is also written as
  `<name>_placements.md` so it can be read without the image.
- Hook into `make_map.py` after the QA sheet; `publish` copies all three files.

## Acceptance

Running the pipeline with no producers implemented yet yields a guide image with the hillshade,
grid, legend, and an empty table. Each sub-issue then only adds a producer.
