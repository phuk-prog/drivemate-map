# Drivemate-map

Builds Drivemate's downloadable map areas. Everything here is our own code, with no third-party packages.

## Code

- `tools/fetch-tiles.mjs` downloads OpenStreetMap-derived vector tiles for a bounding box into `data/cache/`.
- `tools/mvt.mjs` reads Mapbox Vector Tiles.
- `tools/build-area.mjs` turns the tiles into one DMAP file in `data/out/`.
- `tools/graph.mjs` repairs the road graph (joins crossings and road ends).
- `tools/dmap.mjs` reads and writes the DMAP format. `tools/classes.mjs` holds the class-to-code tables.
- `tools/validate-dmap.mjs` checks a DMAP file.
- `tools/preview.mjs` renders a DMAP file to PNG.
- `tools/osm-extract.mjs` reads an OpenStreetMap `.osm.pbf` extract (`tools/osm-pbf.mjs`) and caches turn restrictions, lanes and warnings in `data/osm-cache/`. `tools/restrictions.mjs` and `tools/lanes.mjs` match them to roads.
- `tools/update-regions.mjs` builds new and changed regions listed in `regions.json`.

## Build an area

    node tools/fetch-tiles.mjs --bbox=-0.55,51.28,0.30,51.70 --zoom=14
    node tools/build-area.mjs --name=london --bbox=-0.55,51.28,0.30,51.70 --zoom=14
    node tools/preview.mjs data/out/london.dmap preview.png 1600 1200

## Tests

    node tools/test-graph.mjs
    node tools/test-lanes.mjs
    node tools/test-restrictions.mjs
    node tools/test-speed.mjs
    node tools/test-validate-dmap.mjs
    node tools/test-osm-extract.mjs
    node tools/test-squares.mjs
    node --test tools/test-roundabout-flags.mjs

## App

The app reads DMAP files. Its Kotlin reader must follow `format/FORMAT.md` exactly; `tools/dmap.mjs` is the reference reader. The app draws the labels stored in the file.

Map data © OpenStreetMap contributors, licensed under ODbL. The app must show this attribution.
