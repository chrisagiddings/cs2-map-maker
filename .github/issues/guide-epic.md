## Goal

After importing a heightmap the editor work is still guesswork: where do the water sources go
and at what level, where are the resources, where should the first highway and the utility
connections come in. Produce **one additional contact-sheet image per map**, `<name>_guide.png`,
that answers those with symbols on the playable hillshade, a legend, and a numbered callout table
with the exact in-game coordinates and elevations to type into the editor.

Single visual output, consistent symbolism, symbol key always drawn. The data behind it is also
written as `<name>_placements.json` and `<name>_placements.md` so it survives without the image,
gets committed with the map (#1), and is referenced by the finished-map record (__FIN__).

## Sub-issues

- [ ] __G1__ canvas, symbol set, legend, placement record format (do first; everything else is a producer)
- [ ] __G2__ recommended water sources (border river, stream, lake, sea), builds on #5
- [ ] __G3__ resource deposit locations from the masks, depends on #8
- [ ] __G4__ highway/rail outside connections, initial trunk route, utility connections

## Definition of done

- `make_map.py` writes the guide sheet next to the QA sheet on every run.
- Every symbol on the image appears in the legend; every numbered callout appears in the table
  with in-game x/y (metres from the SW corner), elevation, and a one-line reason.
- Chattanooga guide shows: Tennessee border-river in/out at 63 m, Lookout and S. Chickamauga
  stream sources, I-24/I-75 highway connections, a trunk route between them, power and sewer
  connections, and resource outlines once #8 exists.
- `publish` (#1) includes the guide image and placement files.

## Out of scope

Placing anything in the game automatically. The sheet is advice for a human in the editor; the
finished map is captured by __FIN__.
