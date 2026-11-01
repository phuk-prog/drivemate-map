"""Source-backed building entrance candidates from an OSM GeoJSONSeq export (category 15.2).

Reads the same osmium GeoJSONSeq export the feature packs use. A node is kept only when OSM
tags it as an entrance that is not barred to vehicles, and its nearest drivable road way in the
same export is also open to motor vehicles. The record keeps the road id, distance, and which
side of the road the entrance is on, so the app can pick a reachable entrance. Nothing is
inferred: an entrance with no drivable road in the export is rejected and counted, never guessed.
"""
import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
MAX_ROAD_DISTANCE_M = 40.0
GRID_DEG = 0.002
EARTH_RADIUS_M = 6_371_008.8

DRIVABLE_HIGHWAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
    "service", "living_street", "road", "motorway_link", "trunk_link", "primary_link",
    "secondary_link", "tertiary_link",
}
BARRED = {"no", "private"}
VEHICLE_KEYS = ("access", "motor_vehicle", "motorcar", "vehicle")
NOT_A_DRIVEWAY_ENTRANCE = {"emergency", "exit"}


def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def vehicle_barred(tags):
    return any(str(tags.get(key, "")).strip().lower() in BARRED for key in VEHICLE_KEYS)


def project(point, origin):
    """Local metres east/north of origin (equirectangular, fine at road-entrance scale)."""
    x = math.radians(point[0] - origin[0]) * EARTH_RADIUS_M * math.cos(math.radians(origin[1]))
    y = math.radians(point[1] - origin[1]) * EARTH_RADIUS_M
    return x, y


def nearest_on_segment(point, a, b, origin):
    px, py = project(point, origin)
    ax, ay = project(a, origin)
    bx, by = project(b, origin)
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    cx, cy = ax + t * dx, ay + t * dy
    distance = math.hypot(px - cx, py - cy)
    # Positive cross product: the point is left of the road's direction of travel.
    cross = dx * (py - ay) - dy * (px - ax)
    return distance, cross


def cell(point):
    return (math.floor(point[0] / GRID_DEG), math.floor(point[1] / GRID_DEG))


def load(source):
    nodes, roads = [], []
    text = sys.stdin.read() if str(source) == "-" else Path(source).read_text(encoding="utf-8")
    for raw in text.split("\x1e"):
        raw = raw.strip()
        if not raw:
            continue
        feature = json.loads(raw)
        tags = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        ident = tags.get("@id") or feature.get("id")
        if geometry.get("type") == "Point" and "entrance" in tags:
            nodes.append((str(ident), tuple(geometry["coordinates"][:2]), tags))
        elif geometry.get("type") == "LineString" and tags.get("highway") in DRIVABLE_HIGHWAYS:
            roads.append((str(ident), [tuple(c[:2]) for c in geometry["coordinates"]], tags))
    return nodes, roads


def select(nodes, roads):
    index = {}
    for road_id, line, tags in roads:
        if vehicle_barred(tags):
            continue
        for a, b in zip(line, line[1:]):
            for key in {cell(a), cell(b)}:
                index.setdefault(key, []).append((road_id, a, b, tags))
    records, rejected = [], Counter()
    for node_id, point, tags in nodes:
        kind = str(tags.get("entrance", "")).strip().lower()
        if kind in NOT_A_DRIVEWAY_ENTRANCE:
            rejected["entrance_not_for_drivers"] += 1
            continue
        if vehicle_barred(tags) or str(tags.get("access", "")).strip().lower() in BARRED:
            rejected["entrance_barred"] += 1
            continue
        cx, cy = cell(point)
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for road_id, a, b, road_tags in index.get((cx + dx, cy + dy), []):
                    distance, cross = nearest_on_segment(point, a, b, point)
                    if best is None or distance < best[0]:
                        best = (distance, cross, road_id, road_tags)
        if best is None or best[0] > MAX_ROAD_DISTANCE_M:
            rejected["no_drivable_road_nearby"] += 1
            continue
        distance, cross, road_id, road_tags = best
        records.append({
            "id": node_id, "lon": point[0], "lat": point[1], "entrance": kind,
            "road": road_id, "highway": road_tags.get("highway"), "road_m": round(distance, 1),
            "side": "left" if cross > 0.5 else "right" if cross < -0.5 else "unknown",
        })
    return records, rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, help="GeoJSONSeq OSM export, or - for stdin")
    parser.add_argument("--output", required=True, type=Path, help="JSON output file")
    args = parser.parse_args()
    nodes, roads = load(args.source)
    records, rejected = select(nodes, roads)
    result = {"attribution": ATTRIBUTION, "max_road_distance_m": MAX_ROAD_DISTANCE_M,
              "entrances": sorted(records, key=lambda r: r["id"]),
              "rejected": dict(sorted(rejected.items()))}
    args.output.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"entrances={len(records)} rejected={sum(rejected.values())}")


if __name__ == "__main__":
    main()
