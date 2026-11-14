// Adds new map regions and rebuilds regions whose source has changed.
// Each run builds at most MAX_REGIONS_PER_RUN regions, so runs stay short.
// A region is skipped when its index entry already matches the current source version.
// Usage: node tools/update-regions.mjs

import { readFile, writeFile, copyFile, mkdir, stat } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { sourceVersion, fetchTiles } from "./fetch-tiles.mjs";
import { buildArea } from "./build-area.mjs";
import { buildSquares } from "./build-squares.mjs";
import { prepareOsm } from "./osm-prepare.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PUBLISHED = join(ROOT, "data", "published");
const MAX_REGIONS_PER_RUN = Number(process.env.MAX_REGIONS_PER_RUN ?? 2);
// Squares built in one run over all square areas. Measured: about 1 s per square, so this is about upload size, not build time.
let squaresLeft = Number(process.env.MAX_SQUARES_PER_RUN ?? 100);
// On CI the .dmap files are not in the checkout; trust the index (the release holds the files).
const TRUST_INDEX = process.env.TRUST_INDEX === "1";
// Change this when the builder changes output, so existing regions are rebuilt.
const BUILDER_VERSION = "graph-repair-5-roundabouts";

const config = JSON.parse(await readFile(join(ROOT, "regions.json"), "utf8"));
const regions = Array.isArray(config) ? config : config.regions;
const squareAreas = Array.isArray(config) ? [] : config.squareAreas ?? [];
const indexFile = join(PUBLISHED, "index.json");
let index = {};
try {
  index = JSON.parse(await readFile(indexFile, "utf8"));
} catch {
  index = {};
}

await mkdir(PUBLISHED, { recursive: true });
const { version } = await sourceVersion();
console.log(`source version ${version}`);

// A region or square area may carry "osm": { "geofabrik": "<extract id>" } to add turn restrictions, lanes
// and warnings from that Geofabrik extract. "enabled": false keeps an entry out of every run.
const builderFor = (r) => (r.basic ? `${BUILDER_VERSION}+basic` : r.osm ? `${BUILDER_VERSION}+osm1` : BUILDER_VERSION);

let built = 0;
for (const region of regions) {
  if (region.enabled === false) {
    console.log(`${region.id}: disabled in regions.json, skipped`);
    continue;
  }
  const builder = builderFor(region);
  const out = join(PUBLISHED, `${region.id}.dmap`);
  const exists = TRUST_INDEX || (await stat(out).then(() => true, () => false));
  const previous = index[region.id];

  if (previous && previous.version === version && previous.builder === builder && exists) {
    index[region.id].url = `https://github.com/${process.env.GITHUB_REPOSITORY ?? "phuk-prog/Drivemate-map"}/releases/download/maps-latest/${region.id}.dmap`;
    console.log(`${region.id}: up to date`);
    continue;
  }
  if (built >= MAX_REGIONS_PER_RUN) {
    console.log(`${region.id}: waiting for a later run`);
    continue;
  }

  console.log(`${region.id}: ${previous ? "source changed, rebuilding" : "new region, building"}`);
  if (region.osm) await prepareOsm({ geofabrik: region.osm.geofabrik, name: region.id, bbox: region.bbox });
  await fetchTiles(region.bbox, region.zoom, { log: () => {} });
  // A basic region skips junction joining (see buildArea); its routing is not used.
  await buildArea({ name: region.id, bbox: region.bbox, zoom: region.zoom, version, joinRoads: !region.basic, log: () => {} });
  await copyFile(join(ROOT, "data", "out", `${region.id}.dmap`), out);
  index[region.id] = {
    version,
    builder,
    zoom: region.zoom,
    bbox: region.bbox,
    url: `https://github.com/${process.env.GITHUB_REPOSITORY ?? "phuk-prog/Drivemate-map"}/releases/download/maps-latest/${region.id}.dmap`,
    bytes: (await stat(out)).size,
    builtAt: new Date().toISOString(),
  };
  built++;
}

await writeFile(indexFile, JSON.stringify(index, null, 2) + "\n");
console.log(`built ${built} region(s)`);

// Grid squares for drawn-area downloads. Each square is rebuilt only when the source or builder changes.
for (const area of squareAreas) {
  if (area.enabled === false) {
    console.log(`squares ${area.id}: disabled in regions.json, skipped`);
    continue;
  }
  const bbox = [area.bbox[0], area.bbox[1], area.bbox[2], area.bbox[3]];
  const r = await buildSquares({ bbox, zoom: area.zoom, osm: area.osm ? area.id : undefined, maxBuild: squaresLeft, beforeBuild: area.osm ? () => prepareOsm({ geofabrik: area.osm.geofabrik, name: area.id, bbox: area.bbox }) : undefined, log: console.log });
  squaresLeft -= r.built;
}
