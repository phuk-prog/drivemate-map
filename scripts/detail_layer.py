"""Road furniture and signs for the map (plan test 2.14, owner 18 Oct 2026: "include all speed signs, road
signs ... and more").

Reads `osmium export -f geojsonseq` of the sign and furniture nodes in DriveMate's own OpenStreetMap snapshot
and writes one GeoJSON feature per line with a plain `kind` (what the map draws), `text` (a speed-limit sign's
number, a junction number) and `facing` (the OSM direction or forward/backward, when mapped). Everything
mapped is kept; only nodes that are none of these kinds are dropped.

    python3 scripts/detail_layer.py signs.geojsonseq detail.geojsonseq   # prints a count per kind
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

# OSM node tags -> kind, most specific first
HIGHWAY = {"traffic_signals": "traffic_signals", "stop": "stop", "give_way": "give_way", "crossing": "crossing",
           "speed_camera": "speed_camera", "mini_roundabout": "mini_roundabout", "motorway_junction": "motorway_junction",
           "turning_circle": "turning_circle", "turning_loop": "turning_circle", "passing_place": "passing_place"}
SPEED_SIGN = re.compile(r"(?:^|[;,])\s*(?:GB:670|GB:671|UK:670|maxspeed)\s*(?:\[(\d+)\])?", re.I)


def classify(tags: dict) -> tuple[str, str] | None:
    """(kind, text) for one node's tags, or None when it is nothing the map draws."""
    sign = tags.get("traffic_sign", "")
    m = SPEED_SIGN.search(sign) if sign else None
    if m or (sign == "maxspeed"):
        return "speed_limit_sign", (m and m.group(1)) or _number(tags.get("maxspeed", ""))
    hw = tags.get("highway")
    if hw in HIGHWAY:
        return HIGHWAY[hw], tags.get("ref", "") if hw == "motorway_junction" else ""
    if tags.get("railway") in ("level_crossing", "crossing"):
        return "level_crossing", ""
    if "traffic_calming" in tags:
        return "traffic_calming", ""
    if tags.get("enforcement") or tags.get("highway") == "speed_display":
        return "speed_camera", ""
    if "barrier" in tags and tags["barrier"] not in ("kerb",):
        return "barrier", ""
    if sign:
        return "road_sign", sign[:40]
    return None


def _number(text: str) -> str:
    m = re.match(r"\s*(\d+)", text or "")
    return m.group(1) if m else ""


def main(src: str, dst: str) -> int:
    counts = Counter()
    with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8") as out:
        for line in f:
            feat = json.loads(line.lstrip("\x1e"))
            if feat.get("geometry", {}).get("type") != "Point":
                continue
            tags = feat.get("properties", {})
            got = classify(tags)
            if not got:
                continue
            kind, text = got
            props = {"kind": kind}
            if text:
                props["text"] = text
            facing = tags.get("direction") or tags.get("traffic_signals:direction") or tags.get("traffic_sign:direction")
            if facing:
                props["facing"] = facing
            out.write(json.dumps({"type": "Feature", "geometry": feat["geometry"], "properties": props},
                                 separators=(",", ":")) + "\n")
            counts[kind] += 1
    for kind, n in counts.most_common():
        print(f"{kind}: {n:,}")
    return 0 if counts else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
