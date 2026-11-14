// Builds one DMAP (version 2) area file from cached OpenStreetMap vector tiles.
// Usage: node tools/build-area.mjs --name=london --bbox=-0.55,51.28,0.30,51.70 --zoom=14
// Output: data/out/<name>.dmap (not committed; published to static hosting later)

import { readdir, readFile, writeFile, mkdir, stat } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { decodeTile } from "./mvt.mjs";
import { readCachedTile, tilesForBbox } from "./fetch-tiles.mjs";
import { writeDmap, toFixed7 } from "./dmap.mjs";
import { splitAtSharedVertices, joinCrossings, joinRoadEnds, dropSeamDuplicates, dropDuplicateEdges } from "./graph.mjs";
import { matchRestrictions } from "./restrictions.mjs";
import { matchSpeedLimits } from "./speed.mjs";
import { matchLanes, parseLaneOverpass } from "./lanes.mjs";
import { markRoundaboutEdges, FLAG_ROUNDABOUT } from "./roundabouts.mjs";
import { ROAD_CLASS, LINE_KIND, POLYGON_KIND, PLACE_CATEGORY, POI_CATEGORY, LABEL_KIND, roadLineKind } from "./classes.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SNAP_TOLERANCE = 200; // in 1e-7 degree units: about 2 m

// Tile-space point to [lat, lon] in 1e-7 degree units.
function tileToFixed(z, tx, ty, px, py, extent) {
  const n = 2 ** z;
  const x = tx + px / extent;
  const y = ty + py / extent;
  const lon = (x / n) * 360 - 180;
  const lat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI;
  return [toFixed7(lat), toFixed7(lon)];
}

function insideTile(px, py, extent) {
  return px >= 0 && px <= extent && py >= 0 && py <= extent;
}

// The part of segment p->q inside the tile box [0, extent], or null if none. A segment that
// crosses a tile edge is cut at the edge, so the neighbouring tile holds the other part.
// The far edges (x = extent and y = extent) are not kept, so a line lying exactly on a
// tile edge belongs to one tile only: the tile on its near side.
function clipToTile(p, q, extent) {
  const dx = q[0] - p[0];
  const dy = q[1] - p[1];
  let t0 = 0;
  let t1 = 1;
  const limits = [
    [-dx, p[0]],
    [dx, extent - p[0]],
    [-dy, p[1]],
    [dy, extent - p[1]],
  ];
  for (const [d, room] of limits) {
    if (d === 0) {
      if (room < 0) return null;
    } else {
      const r = room / d;
      if (d < 0) {
        if (r > t1) return null;
        if (r > t0) t0 = r;
      } else {
        if (r < t0) return null;
        if (r < t1) t1 = r;
      }
    }
  }
  const snap = (v) => (Math.abs(v) < 1e-6 ? 0 : Math.abs(v - extent) < 1e-6 ? extent : v);
  const a = t0 === 0 ? p : [snap(p[0] + t0 * dx), snap(p[1] + t0 * dy)];
  const b = t1 === 1 ? q : [snap(p[0] + t1 * dx), snap(p[1] + t1 * dy)];
  return { a, b };
}

// Split a line into runs of the parts owned by this tile (see clipToTile). Each segment is kept
// by one tile only, so a road that crosses a tile edge is not drawn twice. The two runs meet at
// the crossing point, which the vertex snap joins across the seam.
export function ownedRuns(points, extent) {
  const runs = [];
  let current = null;
  for (let i = 0; i + 1 < points.length; i++) {
    const piece = clipToTile(points[i], points[i + 1], extent);
    const far =
      piece && ((piece.a[0] === extent && piece.b[0] === extent) || (piece.a[1] === extent && piece.b[1] === extent));
    if (!piece || far) {
      if (current) runs.push(current);
      current = null;
      continue;
    }
    const { a, b } = piece;
    if (a[0] === b[0] && a[1] === b[1]) continue; // touches the tile at a single point
    const last = current && current[current.length - 1];
    if (last && last[0] === a[0] && last[1] === a[1]) {
      current.push(b);
    } else {
      if (current) runs.push(current);
      current = [a, b];
    }
  }
  if (current) runs.push(current);
  return runs.filter((r) => r.length >= 2);
}

class NodeIndex {
  constructor() {
    this.nodes = [];
    this.cells = new Map();
  }

  // Returns the ID of an existing node within SNAP_TOLERANCE, or creates one.
  get([lat, lon]) {
    const ci = Math.floor(lat / SNAP_TOLERANCE);
    const cj = Math.floor(lon / SNAP_TOLERANCE);
    for (let di = -1; di <= 1; di++) {
      for (let dj = -1; dj <= 1; dj++) {
        const ids = this.cells.get(`${ci + di},${cj + dj}`);
        if (!ids) continue;
        for (const id of ids) {
          const n = this.nodes[id];
          if (Math.abs(n.lat - lat) <= SNAP_TOLERANCE && Math.abs(n.lon - lon) <= SNAP_TOLERANCE) {
            return id;
          }
        }
      }
    }
    const id = this.nodes.length;
    this.nodes.push({ lat, lon });
    const key = `${ci},${cj}`;
    if (!this.cells.has(key)) this.cells.set(key, []);
    this.cells.get(key).push(id);
    return id;
  }
}

// 3D attributes from OpenMapTiles building tags. Heights are stored in decimetres; 0 means unknown.
function buildingAttrs(tags) {
  const h = Number(tags.render_height);
  const m = Number(tags.render_min_height);
  const hex = typeof tags.colour === "string" && /^#[0-9a-f]{6}$/i.test(tags.colour) ? parseInt(tags.colour.slice(1), 16) : 0;
  return {
    height: h > 0 ? Math.min(65535, Math.round(h * 10)) : 0,
    minHeight: m > 0 ? Math.min(65535, Math.round(m * 10)) : 0,
    colour: hex,
  };
}

// Representative point of a feature in tile space: the middle vertex of its longest part.
function anchorPoint(parts) {
  let best = null;
  for (const part of parts) if (!best || part.length > best.length) best = part;
  if (!best || best.length === 0) return null;
  return best[Math.floor(best.length / 2)];
}

// Average of the first ring's vertices, used to place a name inside an area.
function centroidPoint(parts) {
  const ring = parts[0];
  if (!ring || ring.length === 0) return null;
  let sx = 0;
  let sy = 0;
  for (const [x, y] of ring) {
    sx += x;
    sy += y;
  }
  return [sx / ring.length, sy / ring.length];
}

// osmName: which data/osm-cache/*/<osmName>.json to read (a grid square uses its region's caches).
// joinRoads: false skips the junction joining (joinCrossings, joinRoadEnds). Used for the basic world layer,
// whose whole-world grid is too large for one pass. Roads then meet only where their own points coincide.
export async function buildArea({ name, bbox, zoom, version, osmName = name, joinRoads = true, log = console.log }) {
  const cacheRoot = join(ROOT, "data", "cache", version);
  const tiles = tilesForBbox(bbox, zoom);
  const nodes = new NodeIndex();
  const edges = [];
  const lines = [];
  const polygons = [];
  const polygonKeys = new Set();
  const labels = [];
  const labelKeys = new Set();
  const counts = { edges: {}, lines: {}, polygons: {}, labels: {} };
  let dropped = 0;

  const addLabel = (kind, category, [lat, lon], text) => {
    const key = `${kind}|${text}|${lat}|${lon}`;
    if (labelKeys.has(key)) return;
    labelKeys.add(key);
    labels.push({ kind, category, lat, lon, name: text });
    counts.labels[kind] = (counts.labels[kind] || 0) + 1;
  };

  for (const t of tiles) {
    const buf = await readCachedTile(cacheRoot, t.z, t.x, t.y);
    if (!buf || buf.length === 0) continue;
    const byName = Object.fromEntries(decodeTile(buf).map((l) => [l.name, l]));
    const toFixed = (px, py, extent) => tileToFixed(t.z, t.x, t.y, px, py, extent);

    // Roads: routable edges.
    const transport = byName.transportation;
    if (transport) {
      for (const f of transport.features) {
        if (f.type !== 2) continue;
        const roadClass = ROAD_CLASS[f.tags.class];
        if (!roadClass) continue;
        let flags = 0;
        if (f.tags.brunnel === "bridge") flags |= 2;
        if (f.tags.brunnel === "tunnel") flags |= 4;
        if (f.tags.toll) flags |= 8;
        if (f.tags.access === "no" || f.tags.access === "private") flags |= 16;
        if (f.tags.junction === 'roundabout' || f.tags.junction === 'circular') flags |= FLAG_ROUNDABOUT;
        const ow = f.tags.oneway;
        const reverse = ow === -1;
        if (ow === 1 || ow === -1) flags |= 1;

        for (const part of f.parts) {
          for (const run of ownedRuns(part, transport.extent)) {
            const ordered = reverse ? run.slice().reverse() : run;
            const points = ordered.map(([x, y]) => toFixed(x, y, transport.extent));
            const from = nodes.get(points[0]);
            const to = nodes.get(points[points.length - 1]);
            if (from === to && points.length < 3) {
              dropped++;
              continue;
            }
            // OSM layer: roads at different layers are not joined where they cross (tools/graph.mjs).
            const layer = Number(f.tags.layer) || 0;
            edges.push({ from, to, roadClass, flags, points, layer });
            counts.edges[f.tags.class] = (counts.edges[f.tags.class] || 0) + 1;
          }
        }
      }
    }

    // Lines that are not routed: rail, transit, ferries, piers, footpaths, waterways, boundaries.
    const lineSources = [
      { layer: "transportation", kindOf: (f) => (f.type === 2 ? roadLineKind(f.tags.class, f.tags.subclass) : null), level: () => 0 },
      { layer: "waterway", kindOf: (f) => (f.type === 2 ? LINE_KIND.waterway[f.tags.class] ?? null : null), level: () => 0 },
      { layer: "boundary", kindOf: (f) => (f.type === 2 ? LINE_KIND.boundary : null), level: (f) => Math.min(255, Number(f.tags.admin_level) || 0) },
      { layer: "aeroway", kindOf: (f) => (f.type === 2 ? LINE_KIND.aeroway : null), level: () => 0 },
    ];
    for (const src of lineSources) {
      const l = byName[src.layer];
      if (!l) continue;
      for (const f of l.features) {
        const kind = src.kindOf(f);
        if (!kind || ROAD_CLASS[f.tags.class] && src.layer === "transportation") continue;
        const level = src.level(f);
        for (const part of f.parts) {
          for (const run of ownedRuns(part, l.extent)) {
            lines.push({ kind, level, points: run.map(([x, y]) => toFixed(x, y, l.extent)) });
            counts.lines[kind] = (counts.lines[kind] || 0) + 1;
          }
        }
      }
    }

    // Areas: landcover, landuse, parks, water, buildings, piers.
    for (const layerName of ["landcover", "landuse", "park", "water", "building", "transportation", "aeroway"]) {
      const l = byName[layerName];
      if (!l) continue;
      for (const f of l.features) {
        if (f.type !== 3) continue;
        const kind = POLYGON_KIND.area(layerName, f.tags);
        if (!kind) continue;
        const inside = f.parts.some((ring) => ring.some(([x, y]) => insideTile(x, y, l.extent)));
        if (!inside) continue;
        const rings = f.parts
          .map((ring) => ring.map(([x, y]) => toFixed(x, y, l.extent)))
          .filter((ring) => ring.length >= 4);
        if (rings.length === 0) continue;
        // Seam duplicates: the same ring can appear in two neighbouring tiles.
        const key = `${kind}|${rings[0][0]}|${rings[0][1]}|${rings[0].length}|${rings.length}`;
        if (polygonKeys.has(key)) continue;
        polygonKeys.add(key);
        const attrs = layerName === "building" ? buildingAttrs(f.tags) : {};
        polygons.push({ kind, rings, height: attrs.height ?? 0, minHeight: attrs.minHeight ?? 0, colour: attrs.colour ?? 0 });
        counts.polygons[kind] = (counts.polygons[kind] || 0) + 1;
      }
    }

    // Names. Each name is kept only if its anchor lies inside this tile.
    const named = (layerName, types) => {
      const l = byName[layerName];
      if (!l) return [];
      return l.features.filter((f) => types.includes(f.type) && f.tags.name).map((f) => ({ f, extent: l.extent }));
    };

    for (const { f, extent } of named("place", [1])) {
      const a = anchorPoint(f.parts);
      if (!a || !insideTile(a[0], a[1], extent)) continue;
      addLabel(LABEL_KIND.place, PLACE_CATEGORY[f.tags.class] ?? 0, toFixed(a[0], a[1], extent), f.tags.name);
    }
    for (const { f, extent } of named("transportation_name", [2])) {
      const a = anchorPoint(f.parts);
      if (!a || !insideTile(a[0], a[1], extent)) continue;
      const category = ROAD_CLASS[f.tags.class] ?? (f.tags.class === "path" ? 9 : 0);
      addLabel(LABEL_KIND.road, category, toFixed(a[0], a[1], extent), f.tags.name);
    }
    for (const { f, extent } of named("water_name", [1, 2])) {
      const a = anchorPoint(f.parts);
      if (!a || !insideTile(a[0], a[1], extent)) continue;
      addLabel(LABEL_KIND.water, f.tags.class === "bay" ? 2 : 1, toFixed(a[0], a[1], extent), f.tags.name);
    }
    for (const { f, extent } of named("park", [3])) {
      const c = centroidPoint(f.parts);
      if (!c || !insideTile(c[0], c[1], extent)) continue;
      addLabel(LABEL_KIND.park, 0, toFixed(c[0], c[1], extent), f.tags.name);
    }
    const housenumbers = byName.housenumber;
    if (housenumbers) {
      for (const f of housenumbers.features) {
        const a = f.parts[0]?.[0];
        const text = f.tags.housenumber;
        if (!a || !text || !insideTile(a[0], a[1], housenumbers.extent)) continue;
        addLabel(LABEL_KIND.housenumber, 0, toFixed(a[0], a[1], housenumbers.extent), String(text));
      }
    }
    for (const { f, extent } of named("poi", [1])) {
      const a = f.parts[0]?.[0];
      if (!a || !insideTile(a[0], a[1], extent)) continue;
      addLabel(LABEL_KIND.poi, POI_CATEGORY[f.tags.class] ?? POI_CATEGORY.other, toFixed(a[0], a[1], extent), f.tags.name);
    }
  }

  const bboxFixed = [toFixed7(bbox[1]), toFixed7(bbox[0]), toFixed7(bbox[3]), toFixed7(bbox[2])];
  // Make every junction a node, so roads that meet mid-line can be routed across.
  // Roads that cross in the middle get a junction at the crossing point, and road ends that
  // stop short of another road's middle are joined to it.
  const crossed = joinRoads ? joinCrossings(edges) : { edges };
  const ended = joinRoads ? joinRoadEnds(crossed.edges) : { edges: crossed.edges };
  const repaired = dropDuplicateEdges(splitAtSharedVertices(ended.edges));
  let roundaboutWays=[];
  try{
    roundaboutWays=JSON.parse(await readFile(join(ROOT,'data','osm-cache','roundabouts',osmName+'.json'),'utf8'));
  }catch{log('no tagged OSM roundabout cache for '+osmName+'; legacy turn logic will apply');}
  const circleMatch=markRoundaboutEdges(repaired.edges,roundaboutWays);
  // Turn restrictions: from the cache made by tools/fetch-restrictions.mjs. None if there is no cache.
  let restrictionRecords = [];
  try {
    restrictionRecords = JSON.parse(await readFile(join(ROOT, "data", "osm-cache", "restrictions", `${osmName}.json`), "utf8"));
  } catch {
    log(`no restriction cache for ${osmName}; run tools/fetch-restrictions.mjs to add turn restrictions`);
  }
  const restrictions = matchRestrictions(repaired.nodes, repaired.edges, restrictionRecords);
  // Drop seam duplicates: the same area from two neighbouring tiles.
  const keptPolygons = dropSeamDuplicates(polygons);
  // Lane guidance: from the cache made by tools/fetch-lanes.mjs. None if there is no cache.
  let laneWays = [];
  try {
    laneWays = JSON.parse(await readFile(join(ROOT, "data", "osm-cache", "lanes", `${osmName}.json`), "utf8"));
  } catch {
    log(`no lane cache for ${osmName}; run tools/fetch-lanes.mjs to add lane guidance`);
  }
  const laneMatch = matchLanes(repaired.edges, laneWays);
  // Road warnings (speed cameras, level crossings, roadworks): from tools/osm-extract.mjs. None if there is no cache.
  let warnings = [];
  try {
    warnings = JSON.parse(await readFile(join(ROOT, "data", "osm-cache", "warnings", `${osmName}.json`), "utf8"));
  } catch {
    log(`no warnings cache for ${osmName}; run tools/osm-extract.mjs to add road warnings`);
  }
  // The cache covers the whole area, but a square (or any smaller build) keeps only the warnings inside its own box.
  warnings = warnings.filter((w) => w.lat >= bboxFixed[0] && w.lat <= bboxFixed[2] && w.lon >= bboxFixed[1] && w.lon <= bboxFixed[3]);
  // Road speed limits (OSM maxspeed): from tools/osm-extract.mjs. None if there is no cache.
  let speedWays = [];
  try {
    speedWays = JSON.parse(await readFile(join(ROOT, "data", "osm-cache", "speed", `${osmName}.json`), "utf8"));
  } catch {
    log(`no speed cache for ${osmName}; run tools/osm-extract.mjs to add speed limits`);
  }
  const speedMatch = matchSpeedLimits(repaired.edges, speedWays);
  const bytes = writeDmap({
    bbox: bboxFixed,
    nodes: repaired.nodes,
    edges: repaired.edges,
    lines,
    polygons: keptPolygons,
    labels,
    restrictions: restrictions.matched,
    lanes: laneMatch.lanes,
    warnings,
    speedLimits: speedMatch.speedLimits,
  });
  const outDir = join(ROOT, "data", "out");
  await mkdir(outDir, { recursive: true });
  const outFile = join(outDir, `${name}.dmap`);
  await writeFile(outFile, bytes);
  const size = (await stat(outFile)).size;
  const report = {
    name,
    version,
    zoom,
    bbox,
    outFile,
    bytes: size,
    nodes: repaired.nodes.length,
    routableEdges: repaired.edges.length,
    crossingsFound: crossed.joins,
    crossingsLayerSkipped: crossed.layerSkipped,
    endJoins: ended.joins,
    endLayerSkipped: ended.layerSkipped,
    restrictionsFetched: restrictionRecords.length,
    restrictionsMatched: restrictions.matched.length,
    restrictionsUnmatched: restrictions.unmatched,
    laneWays: laneWays.length,
    laneEdgesMatched: laneMatch.stats.matched,
    laneStats: laneMatch.stats,
    roundaboutWays:roundaboutWays.length,
    roundaboutEdgesMatched:circleMatch.marked,
    warnings: warnings.length,
    speedRecords: speedWays.length,
    speedWaysUsed: speedMatch.stats.waysUsed,
    speedEdgesMatched: speedMatch.stats.matched,
    speedEdgesUnmatched: repaired.edges.length - speedMatch.stats.matched,
    speedStats: speedMatch.stats,
    lines: lines.length,
    polygons: keptPolygons.length,
    seamDuplicatesDropped: polygons.length - keptPolygons.length,
    labels: labels.length,
    dropped,
    counts,
  };
  log(JSON.stringify(report, null, 2));
  return { outFile, report };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const arg = (n, def) => {
    const hit = process.argv.find((a) => a.startsWith(`--${n}=`));
    return hit ? hit.slice(n.length + 3) : def;
  };
  const cacheDir = join(ROOT, "data", "cache");
  // Tile versions are named like 20261101_080013_pt. Ignore any other folder.
  const versions = (await readdir(cacheDir)).filter((v) => /^\d{8}_\d{6}/.test(v)).sort();
  const version = arg("version", versions[versions.length - 1]);
  const started = Date.now();
  await buildArea({
    name: arg("name", "london"),
    bbox: arg("bbox", "-0.55,51.28,0.30,51.70").split(",").map(Number),
    zoom: Number(arg("zoom", "14")),
    version,
  });
  console.log(`built in ${((Date.now() - started) / 1000).toFixed(1)}s`);
}
