Part of __EPIC__.

## Goal

Recommend where the initial **highway** outside connections and the **utility** connections
(power, water, sewage) should enter the playable area, and sketch the first trunk route.

## Data

- OpenStreetMap via Overpass (no key, cached like everything else): `highway=motorway|trunk|primary`
  ways and `railway=rail` intersecting the playable bbox, and `power=line` for existing
  transmission corridors. Fetch in the site UTM CRS like the NHD layers.
- The burned DEM and slope raster already in the pipeline.

## Recommendations

- **Highway outside connections**: one per point where a real motorway/trunk crosses the
  playable edge, snapped to the edge pixel, with the road's name/ref. If none exist, propose the
  two lowest-slope edge crossings on opposite sides that avoid water.
- **Rail** connections the same way from `railway=rail`.
- **Initial highway trail**: a least-cost path between the two best highway connections over
  a cost surface of slope penalty + water penalty + existing-motorway bonus, drawn as a dashed
  polyline. A suggestion for the first trunk road, not a road network.
- **Utilities**: power connection at the edge point nearest an existing `power=line` crossing,
  else the flattest edge segment away from the highway; water/sewer connection at the edge where
  the largest river *leaves* the playable area (downstream, so sewage outflow is plausible).
- **Ship / air** only when data supports it (navigable NHDArea river or sea for ship; OSM
  `aeroway=aerodrome` inside the playable for air).

## Output

Placement records `kind: connection.highway|rail|ship|air` and `utility.power|water|sewer`,
the trail as a `kind: trail.highway` polyline record, all drawn with the guide symbol set.
Console prints the edge side and in-game metres for each connection.

## Acceptance on Chattanooga

I-24 and I-75 crossings appear as highway connections with their refs; the CSX rail crossing
appears; the sewer connection lands where the Tennessee leaves the playable area.
