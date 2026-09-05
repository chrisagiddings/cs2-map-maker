Part of __EPIC__. Depends on #8 (resource masks).

## Goal

Draw where the resource masks put deposits, and where the editor's painted resources should go
if the masks are not imported, on the guide sheet.

- From each 256² mask, extract connected blobs, rank by area, and emit a placement record per
  blob (`kind: resource.ore|oil|fertile|water`) at the blob centroid with its area in hectares
  and a suggested editor brush size.
- Cap per type (default 6) so the sheet stays readable; the full mask is still in `resources/`.
- Draw as hatched outlines of the blob (not just a point) in the resource colours from the
  symbol set; number the largest three per type in the callout table.
- When a mask is all black (#8 says so honestly), the legend entry reads "none in source data"
  rather than being silently absent.
