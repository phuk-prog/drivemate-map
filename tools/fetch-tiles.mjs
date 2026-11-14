// Downloads OpenStreetMap-derived vector tiles for a bounding box into a local cache.
// Usage: node tools/fetch-tiles.mjs --bbox=-0.55,51.28,0.30,51.70 --zoom=14
// Tiles are cached in data/cache/<version>/<z>/<x>/<y>.pbf and never committed.

import { mkdir, writeFile, readFile, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = "https://tiles.openfreemap.org/planet";
const CONCURRENCY = 8;
const RETRIES = 4;

export function lonLatToTile(lon, lat, z) {
  const n = 2 ** z;
  const x = Math.floor(((lon + 180) / 360) * n);
  const rad = (lat * Math.PI) / 180;
  const y = Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n);
  return { x, y };
}

export function tilesForBbox([minLon, minLat, maxLon, maxLat], z) {
  const a = lonLatToTile(minLon, maxLat, z); // top-left
  const b = lonLatToTile(maxLon, minLat, z); // bottom-right
  const tiles = [];
  for (let x = a.x; x <= b.x; x++) {
    for (let y = a.y; y <= b.y; y++) tiles.push({ z, x, y });
  }
  return tiles;
}

export async function sourceVersion() {
  const res = await fetch(SOURCE);
  if (!res.ok) throw new Error("TileJSON request failed: " + res.status);
  const json = await res.json();
  const template = json.tiles[0];
  // e.g. https://tiles.openfreemap.org/planet/20261101_080013_pt/{z}/{x}/{y}.pbf
  const version = template.split("/")[4];
  return { version, template };
}

async function fetchWithRetry(url) {
  let lastError;
  for (let attempt = 1; attempt <= RETRIES; attempt++) {
    try {
      const res = await fetch(url);
      if (res.status === 404) return null; // no data for this tile (e.g. open sea)
      if (!res.ok) throw new Error("HTTP " + res.status);
      return Buffer.from(await res.arrayBuffer());
    } catch (err) {
      lastError = err;
      await new Promise((r) => setTimeout(r, 500 * attempt * attempt));
    }
  }
  throw new Error(`Failed after ${RETRIES} attempts: ${url}: ${lastError}`);
}

// version: pass the source version when the caller already has it, so each square does not ask for it again.
export async function fetchTiles(bbox, zoom, { log = console.log, version: knownVersion } = {}) {
  const { version, template } = knownVersion
    ? { version: knownVersion, template: `${SOURCE}/${knownVersion}/{z}/{x}/{y}.pbf` }
    : await sourceVersion();
  const cacheRoot = join(ROOT, "data", "cache", version);
  const tiles = tilesForBbox(bbox, zoom);
  let done = 0;
  let downloaded = 0;
  let missing = 0;
  let index = 0;

  async function worker() {
    while (index < tiles.length) {
      const t = tiles[index++];
      const file = join(cacheRoot, String(t.z), String(t.x), `${t.y}.pbf`);
      const exists = await stat(file).then(() => true, () => false);
      if (!exists) {
        const url = template.replace("{z}", t.z).replace("{x}", t.x).replace("{y}", t.y);
        const data = await fetchWithRetry(url);
        await mkdir(dirname(file), { recursive: true });
        await writeFile(file, data ?? Buffer.alloc(0));
        if (data) downloaded++;
        else missing++;
      }
      done++;
      if (done % 100 === 0 || done === tiles.length) log(`tiles ${done}/${tiles.length}`);
    }
  }

  await Promise.all(Array.from({ length: CONCURRENCY }, worker));
  const manifest = { version, zoom, bbox, count: tiles.length, downloaded, missing, source: SOURCE };
  await mkdir(cacheRoot, { recursive: true });
  await writeFile(join(cacheRoot, `manifest-z${zoom}.json`), JSON.stringify(manifest, null, 2));
  return { version, cacheRoot, tiles, manifest };
}

export async function readCachedTile(cacheRoot, z, x, y) {
  const file = join(cacheRoot, String(z), String(x), `${y}.pbf`);
  try {
    return await readFile(file);
  } catch {
    return null;
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const arg = (name, def) => {
    const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
    return hit ? hit.slice(name.length + 3) : def;
  };
  const bbox = arg("bbox", "-0.55,51.28,0.30,51.70").split(",").map(Number);
  const zoom = Number(arg("zoom", "14"));
  const started = Date.now();
  const result = await fetchTiles(bbox, zoom);
  console.log(JSON.stringify(result.manifest, null, 2));
  console.log(`done in ${((Date.now() - started) / 1000).toFixed(1)}s`);
}
