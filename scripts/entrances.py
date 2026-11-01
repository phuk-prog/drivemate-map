"""Per-region entrance extract: every OSM node tagged entrance=* as a sorted TSV.

Reads one OSM extract (PBF) with pyosmium, using the same FileProcessor approach as
restriction_rewrite.py and dmn_coverage_check.py. Output (header first):

  lat        WGS84, 6 decimals
  lon        WGS84, 6 decimals
  entrance   the entrance=* value (main, service, emergency, exit, ...)
  parent     "<housenumber> <street>" from the entrance node, else from the building way
             that contains it, else "way/<id>" for a building without an address, else empty

Only these four columns are written. Names, phone numbers, operators, opening hours and
every other tag are dropped, so the file carries no personal data. Rows are sorted by
coordinates, then entrance kind, then parent, so the same extract always gives the same bytes.

Usage: entrances.py extract.osm.pbf out.tsv
"""
import argparse
import math
import os
from pathlib import Path
import re
import tempfile

HEADER = "lat\tlon\tentrance\tparent"
CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
MAX_KIND = 40
MAX_FIELD = 200


def clean(value, limit=MAX_FIELD):
    """Strip control characters (including tab and newline) and cap the length."""
    if value is None:
        return ""
    return CONTROL.sub(" ", str(value)).strip()[:limit]


def tag_dict(obj):
    return {t.k: t.v for t in obj.tags}


def address(tags):
    number = clean(tags.get("addr:housenumber"))
    street = clean(tags.get("addr:street"))
    return " ".join(x for x in (number, street) if x)


def coordinate(value, limit):
    """Round to 6 decimals; -0.0 becomes 0.0 so the text form is stable."""
    return round(value, 6) + 0.0 if math.isfinite(value) and -limit <= value <= limit else None


def read_entrances(pbf):
    """node id -> (lat, lon, entrance kind, own address) for every valid entrance=* node."""
    import osmium

    found = {}
    for node in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(osmium.filter.KeyFilter("entrance")):
        tags = tag_dict(node)
        kind = clean(tags.get("entrance"), MAX_KIND)
        if not kind or not node.location.valid():
            continue
        lat = coordinate(node.location.lat, 90)
        lon = coordinate(node.location.lon, 180)
        if lat is None or lon is None:
            continue
        found[node.id] = (lat, lon, kind, address(tags))
    return found


def read_containing_ways(pbf, wanted):
    """entrance node id -> (way id, address) of its building (or addressed way).

    When several ways hold one node, a way with an address wins, then the lowest way id.
    """
    import osmium

    best = {}
    if not wanted:
        return {}
    proc = osmium.FileProcessor(str(pbf), osmium.osm.WAY).with_filter(
        osmium.filter.KeyFilter("building", "addr:housenumber"))
    for way in proc:
        refs = [n.ref for n in way.nodes if n.ref in wanted]
        if not refs:
            continue
        label = address(tag_dict(way))
        rank = (not label, way.id)
        for ref in refs:
            if ref not in best or rank < best[ref][0]:
                best[ref] = (rank, way.id, label)
    return {ref: (way_id, label) for ref, (_, way_id, label) in best.items()}


def parent_label(own_address, container):
    if own_address:
        return own_address
    if container is None:
        return ""
    way_id, label = container
    return label or f"way/{way_id}"


def build_rows(pbf):
    entrances = read_entrances(pbf)
    containers = read_containing_ways(pbf, set(entrances))
    rows = []
    for node_id, (lat, lon, kind, own) in entrances.items():
        parent = parent_label(own, containers.get(node_id))
        rows.append((lat, lon, kind, parent))
    rows.sort(key=lambda r: (round(r[0] * 1e6), round(r[1] * 1e6), r[2], r[3]))
    return rows


def write_tsv(rows, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".entrances-", suffix=".tsv", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(HEADER + "\n")
            for lat, lon, kind, parent in rows:
                stream.write(f"{lat:.6f}\t{lon:.6f}\t{kind}\t{parent}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pbf", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    rows = build_rows(args.pbf)
    write_tsv(rows, args.out)
    print(f"Wrote {len(rows)} entrances to {args.out}")


if __name__ == "__main__":
    main()
