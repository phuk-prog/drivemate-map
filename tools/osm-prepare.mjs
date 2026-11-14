// Downloads a Geofabrik extract and writes the restriction, lane and warning caches that the map
// builder reads (data/osm-cache/*/<name>.json). Build-time only. Used by tools/update-regions.mjs.
// Usage: node tools/osm-prepare.mjs --geofabrik=greater-london --name=london --bbox=minLon,minLat,maxLon,maxLat

import { execFileSync } from "node:child_process";
import { mkdir, rm, readFile, writeFile } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// bbox is [minLon, minLat, maxLon, maxLat]. Removes the large .pbf afterwards.
// The extract is ~1.8 GB for Great Britain, and Geofabrik is a free service, so the download and extract are
// skipped when the caches were made from the extract's current Last-Modified date and the same box.
// A marker file records that. If the date cannot be read, the extract is downloaded as before.
export async function prepareOsm({ geofabrik, name, bbox, log = console.log }) {
  const dir = join(ROOT, "data", "osm-src");
  await mkdir(dir, { recursive: true });
  const pbf = join(dir, `${geofabrik}.osm.pbf`);
  const url = execFileSync("node", [join(ROOT, "tools", "geofabrik-url.mjs"), geofabrik], { encoding: "utf8" }).trim();
  const marker = join(ROOT, "data", "osm-cache", `${name}.source.json`);
  const boxKey = bbox.join(",");
  const remote = await fetch(url, { method: "HEAD" }).then((r) => (r.ok ? r.headers.get("last-modified") : null), () => null);
  const previous = await readFile(marker, "utf8").then(JSON.parse, () => null);
  if (remote && previous && previous.lastModified === remote && previous.bbox === boxKey) {
    log(`${name}: OSM caches already made from ${remote}, skipped`);
    return;
  }
  log(`${name}: downloading ${url}`);
  execFileSync("curl", ["-fL", "--retry", "5", "--retry-all-errors", "--retry-delay", "10", "-o", pbf, url], { stdio: "inherit" });
  log(`${name}: extracting restrictions, lanes and warnings`);
  execFileSync(
    "node",
    ["--max-old-space-size=6000", join(ROOT, "tools", "osm-extract.mjs"), `--pbf=${pbf}`, `--name=${name}`, `--bbox=${bbox.join(",")}`],
    { stdio: "inherit" },
  );
  await rm(pbf, { force: true });
  await mkdir(join(ROOT, "data", "osm-cache"), { recursive: true });
  await writeFile(marker, JSON.stringify({ lastModified: remote, bbox: boxKey, url, preparedAt: new Date().toISOString() }, null, 2) + "\n");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const arg = (n) => {
    const hit = process.argv.find((a) => a.startsWith(`--${n}=`));
    return hit ? hit.slice(n.length + 3) : undefined;
  };
  await prepareOsm({ geofabrik: arg("geofabrik"), name: arg("name"), bbox: arg("bbox").split(",").map(Number) });
}
