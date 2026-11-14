// Builds one DMAP file per grid square for a bounding box, into data/published/squares/.
// Usage: node tools/build-squares.mjs --bbox=minLon,minLat,maxLon,maxLat --zoom=14
// Each file is named lat<i>_lon<j>.dmap. The index lists every square with its size.
// The square files are published to GitHub releases, not committed to git. Each entry records
// bytes (the .dmap) and gzBytes (the .dmap.gz the release serves, as gzip -9 makes it).
//
// Index entries:
//   built square:  version, builder, zoom, bbox, bytes, gzBytes, builtAt, release, url
//   empty square:  version, builder, zoom, bbox, empty: true, builtAt  (no routable road in its tiles: open sea, or outside coverage)
// A square is skipped before any tile is fetched when its entry matches the current source version and builder.
// Each release holds SQUARES_PER_RELEASE squares (two files each), because GitHub allows 1,000 assets per release.

import { readFile, copyFile, mkdir, writeFile, stat } from "node:fs/promises";
import { statSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { fetchTiles, sourceVersion, readCachedTile } from "./fetch-tiles.mjs";
import { decodeTile } from "./mvt.mjs";
import { ROAD_CLASS } from "./classes.mjs";
import { buildArea } from "./build-area.mjs";
import { squaresTouching, squareBbox, squareId } from "./grid.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const SQUARES_DIR = join(ROOT, "data", "published", "squares");
const BUILDER_VERSION = "graph-repair-5-roundabouts";
export const SQUARES_PER_RELEASE = 400;
export const LEGACY_RELEASE = "maps-latest";
// Tile checks allowed per square built in one run. Empty squares cost a tile check but no build.
const CHECKS_PER_BUILD = 30;

const repo = () => process.env.GITHUB_REPOSITORY ?? "phuk-prog/Drivemate-map";
export const releaseUrl = (tag, file) => `https://github.com/${repo()}/releases/download/${tag}/${file}`;

// Compressed size of a square file, made the way the update workflow makes its .dmap.gz:
// "gzip -9 -c <file>". The release uploads that file, so this is the size the app downloads.
export function gzipBytes(file) {
  const r = spawnSync("gzip", ["-9", "-c", file], { maxBuffer: 1024 * 1024 * 1024 });
  if (r.status !== 0) throw new Error(`gzip failed for ${file}: ${r.stderr}`);
  return r.stdout.length;
}

// One index entry for a built square file: its size as built, and its compressed size.
export function indexEntry({ version, builder, zoom, bbox, file }) {
  return { version, builder, zoom, bbox, bytes: statSync(file).size, gzBytes: gzipBytes(file), builtAt: new Date().toISOString() };
}

// Release tag for the n-th square given a release slot (0-based): squares-001, squares-002, ...
export function releaseTagFor(n) {
  return `squares-${String(Math.floor(n / SQUARES_PER_RELEASE) + 1).padStart(3, "0")}`;
}

// True when a tile in the list has at least one routable road, the same test the builder uses.
// Water-only tiles (open sea) do not count: a square with no roads is not worth a file.
async function anyTileHasRoads(cacheRoot, tiles) {
  for (const t of tiles) {
    const buf = await readCachedTile(cacheRoot, t.z, t.x, t.y);
    if (!buf || buf.length === 0) continue;
    const transport = decodeTile(buf).find((l) => l.name === "transportation");
    if (transport && transport.features.some((f) => f.type === 2 && ROAD_CLASS[f.tags.class])) return true;
  }
  return false;
}

// Builds every square touching a bounding box given in lon-first order (as build-area uses).
// osm: name of the OSM caches to use for every square (see build-area osmName); builder is suffixed so squares are
// rebuilt when OSM data is switched on.
// maxBuild: build at most this many squares in this call; the rest wait for a later run (the index is the resume point).
export async function buildSquares({ bbox, zoom, osm, beforeBuild, maxBuild = Infinity, log = console.log }) {
  const builder = osm ? `${BUILDER_VERSION}+osm1` : BUILDER_VERSION;
  const [minLon, minLat, maxLon, maxLat] = bbox;
  await mkdir(SQUARES_DIR, { recursive: true });
  const indexFile = join(SQUARES_DIR, "index.json");
  let index = {};
  try {
    index = JSON.parse(await readFile(indexFile, "utf8"));
  } catch {
    index = {};
  }

  // Entries from before releases were recorded are in the legacy release, one asset per file.
  for (const [id, e] of Object.entries(index)) {
    if (e && !e.empty && !e.release) {
      e.release = LEGACY_RELEASE;
      e.url = releaseUrl(LEGACY_RELEASE, `${id}.dmap.gz`);
    }
  }

  // One source-version request for the whole area, not one per square.
  const { version } = await sourceVersion();
  const squares = squaresTouching([minLat, minLon, maxLat, maxLon]);
  // Squares already placed in a release; a new square goes in the next free slot.
  let slots = Object.values(index).filter((e) => e && !e.empty && e.release && e.release.startsWith("squares-")).length;
  const checkCap = maxBuild === Infinity ? Infinity : maxBuild * CHECKS_PER_BUILD;
  let built = 0;
  let checked = 0;
  let empty = 0;
  let skipped = 0;
  let waiting = 0;
  let prepared = false;
  for (const sq of squares) {
    const id = squareId(sq);
    const out = join(SQUARES_DIR, `${id}.dmap`);
    const entry = index[id];
    if (entry && entry.version === version && entry.builder === builder) {
      // On a CI runner the .dmap files are not in the checkout (they live in the release), so the index alone says "built".
      const haveFile = entry.empty || process.env.TRUST_INDEX === "1" || (await stat(out).then(() => true, () => false));
      if (haveFile) {
        skipped++;
        continue;
      }
    }
    if (built >= maxBuild || checked >= checkCap) {
      waiting++;
      continue;
    }
    checked++;
    const [sMinLat, sMinLon, sMaxLat, sMaxLon] = squareBbox(sq);
    const sqBbox = [sMinLon, sMinLat, sMaxLon, sMaxLat];
    const fetched = await fetchTiles(sqBbox, zoom, { log: () => {}, version });
    if (!(await anyTileHasRoads(fetched.cacheRoot, fetched.tiles))) {
      index[id] = { version, builder, zoom, bbox: sqBbox, empty: true, builtAt: new Date().toISOString() };
      empty++;
      continue;
    }
    if (beforeBuild && built === 0 && !prepared) {
      prepared = true;
      await beforeBuild();
    }
    const { report } = await buildArea({ name: id, bbox: sqBbox, zoom, version, osmName: osm || id, log: () => {} });
    if (report.routableEdges === 0) {
      // The tiles have roads, but the builder keeps none inside this square (each road is kept by one tile).
      index[id] = { version, builder, zoom, bbox: sqBbox, empty: true, builtAt: new Date().toISOString() };
      empty++;
      continue;
    }
    await copyFile(join(ROOT, "data", "out", `${id}.dmap`), out);
    // Keep the release of a square that is rebuilt; otherwise take the next free slot.
    const release = entry && entry.release && !entry.empty ? entry.release : releaseTagFor(slots++);
    index[id] = { ...indexEntry({ version, builder, zoom, bbox: sqBbox, file: out }), release, url: releaseUrl(release, `${id}.dmap.gz`) };
    built++;
  }
  await writeFile(indexFile, JSON.stringify(index, null, 2) + "\n");
  log(`squares: ${squares.length} in area, ${built} built, ${empty} empty, ${skipped} already done, ${waiting} waiting for a later run, version ${version}`);
  return { total: squares.length, built, empty, skipped, waiting, version };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const arg = (n, def) => {
    const hit = process.argv.find((a) => a.startsWith(`--${n}=`));
    return hit ? hit.slice(n.length + 3) : def;
  };
  await buildSquares({
    bbox: arg("bbox", "-2.25,53.34,-2.07,53.46").split(",").map(Number),
    zoom: Number(arg("zoom", "14")),
  });
}
