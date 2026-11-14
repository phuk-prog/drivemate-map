# Drivemate-map

Builds Drivemate's downloadable map areas. Everything here is our own code, with no third-party packages.

## Pipeline

1. `tools/fetch-tiles.mjs` downloads OpenStreetMap-derived vector tiles for a bounding box into `data/cache/` (not committed).
2. `tools/mvt.mjs` is our own Mapbox Vector Tile reader.
3. `tools/build-area.mjs` turns the tiles into one DMAP file in `data/out/` (not committed).
4. `tools/dmap.mjs` reads and writes the DMAP format, defined in `format/FORMAT.md`.
5. `tools/preview.mjs` renders a DMAP file to PNG with our own rasteriser, as a check.
6. `tools/osm-extract.mjs` reads an OpenStreetMap `.osm.pbf` extract (for example from Geofabrik) with our own reader (`tools/osm-pbf.mjs`) and caches turn restrictions and lane tags in `data/osm-cache/restrictions/`, `data/osm-cache/lanes/` and `data/osm-cache/warnings/` (kept apart from the map tiles in `data/cache/`). `tools/build-area.mjs` picks them up by area name and matches them to roads by location (`tools/restrictions.mjs`, `tools/lanes.mjs`). The map tiles do not carry these. `tools/fetch-restrictions.mjs` and `tools/fetch-lanes.mjs` do the same job through the Overpass API, but the public servers refuse GitHub's runners, so use the extract. Run `node tools/test-osm-extract.mjs` to check the reader.

## Coverage

Set in `regions.json`:

- **World, basic** (`world`, zoom 7, `"basic": true`): one file for the whole world, 471 MB. It is built without joining roads at crossings, because the whole world does not fit in one pass of the joining step. Roads show, but they are not joined into a routable network, so do not route on this layer.
- **UK, full** (`uk` square area, zoom 14, with the Great Britain OSM extract): one DMAP file per 0.05° grid square that has a routable road. Squares with no road (open sea, for example) are recorded as empty and not built. The bounding box also takes in a strip of the Republic of Ireland's east coast. Northern Ireland is inside the box but not in the Great Britain extract, so it has no turn restrictions, lanes or speed limits.
- **Stockport** (`stockport` region, zoom 14): a single file, kept as a test area. It is inside the UK squares.

Square files go to releases named `squares-001`, `squares-002` and so on, 400 squares each (800 assets, under GitHub's limit of 1,000 per release). Region files go to `maps-latest`. `tools/publish-releases.mjs` uploads them and checks the asset count first.

Build one square area or region by hand, for example Stockport:

    node tools/fetch-tiles.mjs --bbox=-2.25,53.34,-2.07,53.46 --zoom=14
    node tools/build-area.mjs --name=stockport --bbox=-2.25,53.34,-2.07,53.46 --zoom=14
    node tools/validate-dmap.mjs data/out/stockport.dmap

The Stockport file is about 12 MB. Version 7 of the format is current (see `format/FORMAT.md`).

Build the world basic layer by hand (needs about 8 GB of memory, about 3 minutes):

    node tools/fetch-tiles.mjs --bbox=-180,-85.0511,180,85.0511 --zoom=7

## Known limits of the current version

## Data source and licence

Source: OpenStreetMap, read as OpenFreeMap's vector tiles (OpenMapTiles schema). Map data © OpenStreetMap contributors, licensed under ODbL. Any app that uses this data must show that attribution.

- Road topology is built from tile line ends snapped within about 2 m. Roads that cross in the middle, with no shared point, are joined at the crossing (`tools/graph.mjs`, `joinCrossings`). Bridges and tunnels are skipped, using the OSM `brunnel` tag. Roads with different OSM `layer` values are not joined. On Stockport (`--bbox=-2.25,53.34,-2.07,53.46 --zoom=14`) the build finds 39,800 crossings or touches that it joins, and leaves 10 apart because the layers differ (2 crossings and 8 touches). Road ends that stop within 2 m of another road's middle, with no shared point, are joined to it at the nearest point (`joinRoadEnds`), under the same bridge, tunnel and layer rules: on Stockport this joins 6,174 ends and leaves 16 apart by layer. Points within 2 m of each other are one junction: where several junction points lie within 2 m of a new point, they are all merged into one vertex, so the result does not depend on the order the roads are read. Roads that cross at a different height with the same layer value and no bridge or tunnel tag are still joined, so false junctions can remain; this is not measured against imagery. Run `node tools/test-graph.mjs` to check the rules.
- Two-point loop roads (same start and end, two points) are dropped by the builder. Longer loops, such as roundabouts, are kept on purpose.
- Turn restrictions, lanes and speed limits come only from an OSM extract (`osm` in `regions.json`). Areas without one have none.
- Roads that cross a tile seam are kept once. Area features near a seam are kept once when their outer boundary matches. Areas that differ in shape at a seam are not checked, and can still appear twice. On Stockport, 1,657 pairs of same-kind areas overlap partly (measured by sampling, overlap 1% to 95% of the smaller area). Of these, 829 lie within 50 m of a tile seam, and only 77 of those are near the same size, so most are different OSM areas rather than copies.
- Routing gaps: on Stockport the road graph has 195 groups of connected roads, down from 4,156 before road ends were joined to other roads and vertices were merged. 96 of them are single roads with no other road at either end, down from 3,194. 53 of those are service roads, and 16 of the service roads are tagged `access=no` or `access=private` (the map tiles carry no motor-vehicle tag); before the joins they were 2,399 and 971. Of the 192 ends of these single roads, 6 still lie within 2 m of another road's middle without a shared point. All 6 were single road ends before this work too. Traced: 4 are on a road at a different OSM layer (left apart on purpose); 2 are on a bridge (skipped on purpose).
- Labels are stored in the file. The preview tool does not draw text; the app will draw labels.
