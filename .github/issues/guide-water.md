Part of __EPIC__. Builds on #5 (which emits the raw edge crossings); this issue turns them into
editor-ready water source recommendations and draws them on the guide sheet.

## Recommendations to produce

CS2 map editor water source types: **Border River** (constant level, flows in/out at the edge,
snaps to the border), **Border Sea**, **Stream** (emits at a rate inside the map, optionally
seasonal), **Constant Level** (lake).

- **Border River** at every point where a flowline of order >= 6 crosses the playable edge.
  Level = in-game water surface at that point taken from the burned DEM, not the single
  reference surface (a river 10 km upstream is higher). Flow direction from NHD
  `fromnode`/`tonode`.
- **Stream** source at the upstream end of every order 4-5 creek that starts inside the
  playable area or enters it, with a suggested rate scaled from NHD `totdasqkm` (drainage area)
  so big creeks get more water. Cap the count (default 8) by drainage area to keep the sheet
  readable.
- **Constant Level** for every water polygon > 0.5 km² wholly inside the playable area, level =
  its smoothed surface.
- **Border Sea** when an NHDArea SeaOcean/BayInlet touches the edge (coastal, see #6).

## Output

Placement records `kind: water.*` with `elev_m`, flow direction, suggested rate, and the
reason. Drawn with the water symbols from the guide symbol set plus an arrow for flow direction.
Also printed in the console under the height-scale banner, because these numbers get typed into
the editor by hand.

## Acceptance on Chattanooga

Two border-river entries for the Tennessee (inflow and outflow at 63 m), stream sources on
Lookout Creek and South Chickamauga Creek, no sources in the middle of the reservoir.
