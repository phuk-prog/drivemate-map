"""Test 1.5 (owner, 18 Oct 2026): DMN's UK road pieces cover the whole UK, islands and borders too.

Two checks, both against the published index (dmn-uk.json) and its pieces:

* every quarter-degree square where the OpenStreetMap snapshot has car roads has a piece. The
  squares come from `squares`, which reads the snapshot itself (pyosmium), not the piece builder;
* named inhabited islands and border towns each have their square's piece, with roads inside it
  within 2 km of the place (read from the piece itself).

    python3 scripts/dmn_coverage_check.py squares uk.osm.pbf osm-squares.json
    python3 scripts/dmn_coverage_check.py check DIR --osm-squares osm-squares.json   # DIR: index + pieces
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import struct
import sys
from pathlib import Path

CAR_ROADS = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
             "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
             "service"}
MIN_ROAD_POINTS = 5  # a square with fewer road points than this is a sliver of the next one's road
NEAR_M = 2000

# Inhabited islands with public roads, and towns on the land borders and coasts at the edges.
PLACES = {
    "islands": [
        ("Lerwick, Shetland", 60.1550, -1.1450), ("Baltasound, Unst", 60.7600, -0.8500),
        ("Kirkwall, Orkney", 58.9810, -2.9600), ("Stromness, Orkney", 58.9630, -3.2960),
        ("Stornoway, Lewis", 58.2090, -6.3870), ("Tarbert, Harris", 57.8980, -6.8040),
        ("Lochmaddy, North Uist", 57.5970, -7.1610), ("Castlebay, Barra", 56.9550, -7.4870),
        ("Portree, Skye", 57.4130, -6.1960), ("Tobermory, Mull", 56.6230, -6.0700),
        ("Bowmore, Islay", 55.7570, -6.2880), ("Brodick, Arran", 55.5770, -5.1500),
        ("Rothesay, Bute", 55.8360, -5.0550), ("Millport, Great Cumbrae", 55.7520, -4.9300),
        ("Holyhead, Anglesey", 53.3090, -4.6330), ("Newport, Isle of Wight", 50.6990, -1.2930),
        ("Hugh Town, St Mary's", 49.9150, -6.3130), ("Rathlin Island", 55.2930, -6.1950),
        ("Walney Island", 54.0950, -3.2600), ("Hayling Island", 50.7850, -0.9710),
        ("Canvey Island", 51.5230, 0.5800), ("Sheppey (Sheerness)", 51.4410, 0.7630),
        ("Lindisfarne (Holy Island)", 55.6690, -1.7990),
    ],
    "borders": [
        ("Gretna", 54.9940, -3.0650), ("Berwick-upon-Tweed", 55.7700, -2.0070),
        ("Coldstream", 55.6480, -2.2520), ("Chepstow", 51.6420, -2.6740),
        ("Monmouth", 51.8120, -2.7160), ("Chester", 53.1930, -2.8930),
        ("Newry", 54.1750, -6.3400), ("Derry/Londonderry", 54.9970, -7.3090),
        ("Strabane", 54.8270, -7.4630), ("Enniskillen", 54.3440, -7.6390),
        ("Belleek", 54.4780, -8.0960), ("Crossmaglen", 54.0750, -6.6100),
    ],
    "edges": [
        ("Land's End", 50.0660, -5.7140), ("Lizard", 49.9600, -5.2060),
        ("John o' Groats", 58.6440, -3.0690), ("Durness", 58.5680, -4.7450),
        ("Dover", 51.1280, 1.3130), ("Lowestoft", 52.4810, 1.7530),
        ("St Davids", 51.8810, -5.2660), ("Portpatrick", 54.8420, -5.1170),
    ],
}


def key(lat: float, lon: float) -> str:
    return f"{math.floor(lat * 4)}_{math.floor(lon * 4)}"


def metres(a, b) -> float:
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return 6371000 * math.hypot(dlat, dlon)


def pack_nodes(data: bytes) -> list[tuple[float, float]]:
    """The road points of a DMNPACK2 piece (the first section of the layout)."""
    raw = gzip.decompress(data)
    if raw[:8] != b"DMNPACK2":
        raise ValueError("not a DMNPACK2 road pack")
    (n,) = struct.unpack_from("<I", raw, 8)
    return [(lat / 1e7, lon / 1e7) for lat, lon, _ in struct.iter_unpack("<iiq", raw[12:12 + 16 * n])]


def osm_squares(pbf: Path) -> dict[str, int]:
    """Road points per square, read from the snapshot (every node of every car road)."""
    import osmium
    wanted, counts = set(), {}
    for w in osmium.FileProcessor(str(pbf), osmium.osm.WAY).with_filter(osmium.filter.KeyFilter("highway")):
        if w.tags.get("highway") in CAR_ROADS:
            wanted.update(n.ref for n in w.nodes)
    for n in osmium.FileProcessor(str(pbf), osmium.osm.NODE):
        if n.id in wanted and n.location.valid():
            k = key(n.location.lat, n.location.lon)
            counts[k] = counts.get(k, 0) + 1
    return counts


def check(folder: Path, squares: dict[str, int] | None) -> tuple[list[str], list[str]]:
    """(lines of the report, reasons it fails)."""
    index = json.loads((folder / "dmn-uk.json").read_text())
    tiles = index["tiles"]
    lines, fails = [], []
    if squares is not None:
        roads = {k for k, n in squares.items() if n >= MIN_ROAD_POINTS}
        missing = sorted(roads - set(tiles), key=lambda k: tuple(map(int, k.split("_"))))
        lines.append(f"- {'PASS' if not missing else 'FAIL'} every square with car roads has a piece: "
                     f"{len(roads) - len(missing)}/{len(roads)} squares")
        fails += [f"square {k} ({squares[k]} road points in OpenStreetMap) has no piece" for k in missing]
    nodes: dict[str, list] = {}
    for group, places in PLACES.items():
        ok = 0
        for name, lat, lon in places:
            k = key(lat, lon)
            if k not in tiles:
                fails.append(f"{name}: square {k} has no piece")
                continue
            if k not in nodes:
                f = folder / tiles[k]["file"]
                nodes[k] = pack_nodes(f.read_bytes()) if f.exists() else []
            near = min((metres((lat, lon), p) for p in nodes[k]), default=math.inf)
            if near > NEAR_M:
                fails.append(f"{name}: no road within {NEAR_M / 1000:.0f} km in piece {k}"
                             + ("" if nodes[k] else " (piece not downloaded)"))
                continue
            ok += 1
        lines.append(f"- {'PASS' if ok == len(places) else 'FAIL'} {group}: {ok}/{len(places)} have roads")
    return lines, fails


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("squares")
    s.add_argument("pbf")
    s.add_argument("out")
    c = sub.add_parser("check")
    c.add_argument("dir")
    c.add_argument("--osm-squares")
    sub.add_parser("keys")  # the squares the named places need (the job downloads just those)
    a = ap.parse_args()
    if a.cmd == "squares":
        Path(a.out).write_text(json.dumps(osm_squares(Path(a.pbf)), sort_keys=True))
        return 0
    if a.cmd == "keys":
        print("\n".join(sorted({key(lat, lon) for ps in PLACES.values() for _, lat, lon in ps})))
        return 0
    squares = json.loads(Path(a.osm_squares).read_text()) if a.osm_squares else None
    lines, fails = check(Path(a.dir), squares)
    print(f"## Coverage (test 1.5): {'pass' if not fails else 'FAIL'}\n")
    print("\n".join(lines))
    for f in fails[:60]:
        print(f"  - {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
