"""Derive the Yew Street / Edgeley / Morrisons regression fixture from an OSM API 0.6 extract.

Reads an OSM XML file (from https://api.openstreetmap.org/api/0.6/map?bbox=...), keeps only the
roundabouts and the highway ways that touch them, and writes a bounded JSON fixture with the
source hash and attribution. It does not infer exit numbers, turn permissions or missing roads:
an exit is "mapped" only when OSM carries an explicit exit tag on the approach.
"""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
HIGHWAY_CLASSES = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "service", "living_street", "motorway_link", "trunk_link",
    "primary_link", "secondary_link", "tertiary_link",
}
EXIT_TAGS = ("exit", "destination:exit", "junction:ref", "exit_to")
NAME_HINTS = ("Edgeley", "Mercian Way", "Shaw Heath", "Yew", "Menai", "Morrison", "Armoury")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tags_of(element):
    return {tag.get("k"): tag.get("v") for tag in element.findall("tag")}


def build(osm_path, bbox):
    root = ET.parse(osm_path).getroot()
    nodes = {}
    for node in root.findall("node"):
        nodes[node.get("id")] = (float(node.get("lat")), float(node.get("lon")))
    ways = {}
    for way in root.findall("way"):
        ways[way.get("id")] = {
            "nodes": [nd.get("ref") for nd in way.findall("nd")],
            "tags": tags_of(way),
        }

    roundabouts = {
        wid: w for wid, w in ways.items()
        if w["tags"].get("junction") == "roundabout" and w["nodes"]
    }
    ring_nodes = {n for w in roundabouts.values() for n in w["nodes"]}

    approaches = {}
    for wid, way in ways.items():
        if wid in roundabouts or way["tags"].get("highway") not in HIGHWAY_CLASSES:
            continue
        touching = sorted(set(way["nodes"]) & ring_nodes, key=way["nodes"].index)
        if touching:
            approaches[wid] = touching

    named = sorted(
        wid for wid, way in ways.items()
        if any(hint in way["tags"].get("name", "") for hint in NAME_HINTS)
        and wid not in roundabouts
    )

    def way_record(wid, way):
        tags = way["tags"]
        return {
            "id": int(wid),
            "highway": tags.get("highway"),
            "name": tags.get("name"),
            "ref": tags.get("ref"),
            "oneway": tags.get("oneway"),
            "junction": tags.get("junction"),
            "nodes": [int(n) for n in way["nodes"]],
            "exit_tags": {k: tags[k] for k in EXIT_TAGS if k in tags},
        }

    return {
        "fixture": "uk-yew-street-edgeley",
        "source": {
            "service": "OpenStreetMap API 0.6 map call (bbox)",
            "bbox_lon_lat": bbox,
            "raw_extract_sha256": sha256(osm_path),
            "attribution": ATTRIBUTION,
            "method": "explicit OSM tags only; no exit counts or turn permissions inferred",
        },
        "coverage": {
            "node_count_in_extract": len(nodes),
            "roundabout_count": len(roundabouts),
            "ring_node_count": len(ring_nodes),
            "approach_way_count": len(approaches),
            "named_hint_way_count": len(named),
        },
        "roundabouts": [
            {
                "id": int(wid),
                "name": w["tags"].get("name"),
                "ref": w["tags"].get("ref"),
                "highway": w["tags"].get("highway"),
                "closed": w["nodes"][0] == w["nodes"][-1],
                "node_ids": [int(n) for n in w["nodes"]],
                "centroid": [
                    round(sum(nodes[n][0] for n in w["nodes"] if n in nodes) / len(w["nodes"]), 7),
                    round(sum(nodes[n][1] for n in w["nodes"] if n in nodes) / len(w["nodes"]), 7),
                ],
                "exit_count_mapped": any(k in w["tags"] for k in EXIT_TAGS),
            }
            for wid, w in sorted(roundabouts.items(), key=lambda kv: int(kv[0]))
        ],
        "approach_ways": [
            {"way": way_record(wid, ways[wid]), "joins_ring_at_nodes": [int(n) for n in touching]}
            for wid, touching in sorted(approaches.items(), key=lambda kv: int(kv[0]))
        ],
        "named_hint_ways": [way_record(wid, ways[wid]) for wid in named],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("osm_xml", type=Path)
    parser.add_argument("--bbox", required=True, help="min_lon,min_lat,max_lon,max_lat")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    bbox = [float(v) for v in args.bbox.split(",")]
    if len(bbox) != 4:
        raise SystemExit("bbox needs four values")
    fixture = build(args.osm_xml, bbox)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(fixture["coverage"]))


if __name__ == "__main__":
    main()
