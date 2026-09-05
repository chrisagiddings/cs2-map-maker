# Local elevation sources

National DTMs that sit behind a registration wall cannot be fetched by the pipeline. Download
them yourself, drop the GeoTIFFs here, and declare them in `sources.json` (this folder is
gitignored except for this README and `sources.example.json`):

```json
[
 {"name": "gsi_10m", "product": "GSI Fundamental Geospatial Data 10 m", "files": ["data/local/gsi/*.tif"],
  "kind": "dtm", "native_m": 10, "vertical_datum": "JGD2011 orthometric", "nodata": -9999},
 {"name": "tinitaly", "product": "TINITALY 10 m", "files": ["data/local/tinitaly/*.tif"],
  "kind": "dtm", "native_m": 10, "vertical_datum": "EGM2008 (approx)", "nodata": -9999}
]
```

The resolver prefers the finest DTM that fully covers the extent, so a local 10 m DTM beats the
global 30 m Copernicus DSM automatically. Any CRS works; files are mosaicked and warped to the
site UTM grid.

Where to get them:
- Japan: GSI Fundamental Geospatial Data DEM 5A/5B/10B, https://fgd.gsi.go.jp/download/ (free registration)
- Italy: TINITALY 10 m, https://tinitaly.pi.ingv.it/ (free registration)
- Europe: EU-DTM 30 m via OpenTopography (`EU_DTM`) with a free API key
