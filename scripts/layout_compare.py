"""Test 2.17: our own map layout against the OpenMapTiles-layout map it replaces, on the whole UK.

    python3 scripts/layout_compare.py OLD.pmtiles NEW.pmtiles > summary.md

No bigger: whole file and bytes per zoom. Nothing a driver needs lost: around every UK probe in
coverage_audit.py (all four nations, cities and remote places), at zooms 8, 10, 12 and 14 (3x3 tiles each),
the road length of every road type the style draws, and the road, place and named-place names. Exit 1 on
a loss: road length under LENGTH_KEPT of the old, or names under NAMES_KEPT, or a bigger file.
"""
import gzip
import importlib.util
import math
import sys
from collections import Counter
from pathlib import Path

LENGTH_KEPT = 0.98  # simplification and line merging may differ a little; anything more is a loss
NAMES_KEPT = 0.98
ZOOMS = (8, 10, 12, 14)
ROADS = ("motorway", "trunk", "primary", "secondary", "tertiary", "minor", "service", "ferry", "busway", "construction")
POIS = {"fuel": "fuel", "charging_station": "charging", "parking": "parking", "hospital": "hospital"}


def road_visible(kind, zoom):
    return kind not in ('busway', 'construction') or zoom >= 13


def place_visible(kind, zoom):
    return zoom >= {'city': 6, 'town': 8, 'village': 11, 'suburb': 12}.get(kind, 99)


def probes():
    spec = importlib.util.spec_from_file_location("coverage_audit", Path(__file__).with_name("coverage_audit.py"))
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    for nation, core in audit.NATION_PROBES.items():
        yield from ((nation, *p) for p in (core, *audit.REGIONAL_PROBES[nation]))


def tile_of(z, lat, lon):
    n = 2 ** z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def value(v):
    for field, got in v.ListFields():
        return got
    return None


def props(layer, feature):
    return {layer.keys[feature.tags[i]]: value(layer.values[feature.tags[i + 1]]) for i in range(0, len(feature.tags), 2)}


def length(feature, extent):
    """Line length in tiles (MVT geometry commands: MoveTo, LineTo, ClosePath; zigzag deltas)."""
    total, x, y, i, g = 0.0, 0, 0, 0, feature.geometry
    while i < len(g):
        cmd, count = g[i] & 7, g[i] >> 3
        i += 1
        for _ in range(count if cmd != 7 else 0):
            dx, dy = (g[i] >> 1) ^ -(g[i] & 1), (g[i + 1] >> 1) ^ -(g[i + 1] & 1)
            i += 2
            if cmd == 2:
                total += math.hypot(dx, dy)
            x, y = x + dx, y + dy
    return total / extent


def road_kind(name, p):
    """The road type a feature is drawn as, in either layout (None: not drawn as a road)."""
    if name == "roads":
        return p.get("kind")
    if name == "transportation":
        c = p.get("class", "")
        if c in ("bus_guideway", "busway"):
            return "busway"
        if c.endswith("_construction"):
            return "construction"
        return c if c in ROADS else None
    return None


def name_group(layer, p, zoom):
    if not p.get('name'):
        return None
    road_layer = 'transportation' if layer == 'transportation_name' else layer
    if zoom == 14 and road_kind(road_layer, p):
        return 'road'
    if layer in ('place', 'places') and place_visible(p.get('class', p.get('kind')), zoom):
        return 'place'
    # POI markers start at display zoom 15. Compare their z14 source data in both maps,
    # rather than overview tiles covering much larger areas which the app never labels.
    if zoom == 14 and (layer == 'pois' or layer == 'poi' and p.get('subclass') in POIS):
        return 'poi'
    return None


def sample(path):
    from mapbox_vector_tile.Mapbox import vector_tile_pb2
    from pmtiles.reader import MmapSource, Reader
    lengths, names = Counter(), {"road": set(), "place": set(), "poi": set()}
    with open(path, "rb") as f:
        reader = Reader(MmapSource(f))
        for _, _, lat, lon in probes():
            for z in ZOOMS:
                cx, cy = tile_of(z, lat, lon)
                for x in (cx - 1, cx, cx + 1):
                    for y in (cy - 1, cy, cy + 1):
                        data = reader.get(z, x, y)
                        if not data:
                            continue
                        tile = vector_tile_pb2.tile()
                        tile.ParseFromString(gzip.decompress(data))
                        for layer in tile.layers:
                            for feature in layer.features:
                                p = props(layer, feature)
                                kind = road_kind(layer.name, p)
                                if kind:
                                    lengths[(z, kind)] += length(feature, layer.extent)
                                group = name_group(layer.name, p, z)
                                if group:
                                    names[group].add(p['name'])
    return lengths, names


def sizes(path):
    from pmtiles.reader import MmapSource, all_tiles
    per_zoom = Counter()
    with open(path, "rb") as f:
        for (z, _, _), data in all_tiles(MmapSource(f)):
            per_zoom[z] += len(data)
    return Path(path).stat().st_size, per_zoom


def main(old, new):
    problems = []
    (old_bytes, old_zoom), (new_bytes, new_zoom) = sizes(old), sizes(new)
    print("## Test 2.17: our own map layout against the OpenMapTiles layout (whole UK)\n")
    print(f"Whole file: old {old_bytes / 1e9:.2f} GB, new {new_bytes / 1e9:.2f} GB "
          f"({(new_bytes - old_bytes) / old_bytes:+.1%})\n")
    if new_bytes > old_bytes:
        problems.append("the new file is bigger")
    print("| zoom | old MB | new MB | change |\n|---|---|---|---|")
    for z in sorted(set(old_zoom) | set(new_zoom)):
        o, n = old_zoom[z] / 1e6, new_zoom[z] / 1e6
        print(f"| {z} | {o:.1f} | {n:.1f} | {(n - o) / o:+.0%} |" if o else f"| {z} | 0 | {n:.1f} | new |")
    (old_len, old_names), (new_len, new_names) = sample(old), sample(new)
    print(f"\nRoad length around {len(list(probes()))} UK places (3x3 tiles each), in tile widths:\n")
    print("| zoom | road | old | new | kept |\n|---|---|---|---|---|")
    for z in ZOOMS:
        for kind in ROADS:
            if not road_visible(kind, z):
                continue
            o, n = old_len[(z, kind)], new_len[(z, kind)]
            if not o and not n:
                continue
            kept = n / o if o else 1.0
            print(f"| {z} | {kind} | {o:.1f} | {n:.1f} | {kept:.1%} |")
            if kept < LENGTH_KEPT:
                problems.append(f"{kind} roads at zoom {z}: {kept:.1%} of the old length")
    print("\n| names | old | new | kept |\n|---|---|---|---|")
    for what in ("road", "place", "poi"):
        o, n = old_names[what], new_names[what]
        kept = len(o & n) / len(o) if o else 1.0
        print(f"| {what} | {len(o)} | {len(n)} | {kept:.1%} |")
        if kept < NAMES_KEPT:
            problems.append(f"{what} names: {kept:.1%} kept; missing e.g. {sorted(o - n)[:5]}")
    print("\n" + ("**PASS**" if not problems else "**FAIL**: " + "; ".join(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))

