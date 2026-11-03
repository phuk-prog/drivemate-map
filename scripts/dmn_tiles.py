"""DMN's UK road map in pieces for the DriveMate app: it uses the online
map, downloading and keeping the bits needed.

Squares of a quarter degree (dmn-<floor(lat*4)>_<floor(lon*4)>.dmnpack, every road) and one UK
main-roads piece (dmn-main-uk.dmnpack: motorways and trunk roads), all written by DMN's map
reader (drivemate-tests tools/dmn_pack.py) from the UK OpenStreetMap extract. Each square is
read from an extract with a margin, so dead ends, barriers and turn bans at its edge are read as in
the whole map; the phone joins squares by OSM node id.

P15.2: each square also gets its OSM entrance points (dmn-ent-<y>_<x>.tsv.gz: nodes tagged
entrance=* or amenity=parking_entrance inside the square, their tags copied as they are), so the
phone can end a route on the road nearest the destination's entrance. Nothing is inferred.

    python3 scripts/dmn_tiles.py band uk.osm.pbf --rows 199:204 --dmn DMN_DIR --out out/
    python3 scripts/dmn_tiles.py main uk.osm.pbf --dmn DMN_DIR --out out/
    python3 scripts/dmn_tiles.py index out/ --source TAG
"""
import argparse
import concurrent.futures
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
from pathlib import Path

MARGIN = 0.03  # degrees read around each square (about 3 km)
WEST, EAST = -8.75, 2.0  # UK and Ireland's island of Ireland edge to the North Sea
BLOCK = 2  # degrees: main roads are also cut into blocks this size (8 squares of a quarter degree)
MIN_BYTES = 2_000  # an extract smaller than this has no roads worth a piece
ENT_HEADER = "#drivemate-entrances"
ENT_COLUMNS = ("osm_node", "lat_e7", "lon_e7", "entrance", "amenity", "access")
ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"


def run(*cmd, **kw):
    print("+", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(list(map(str, cmd)), check=True, **kw)


def pack(dmn, pbf, out, extra):
    r = subprocess.run([sys.executable, "-m", "tools.dmn_pack", str(Path(pbf).resolve()), str(Path(out).resolve()), *extra],
                       cwd=Path(dmn).resolve(), capture_output=True, text=True)
    if r.returncode and "No motorcar graph edges" in r.stderr:
        return None  # only footpaths and the like here
    if r.returncode:
        raise RuntimeError(f"{out.name}: {r.stderr[-2000:]}")
    return json.loads(r.stdout)


def road_squares(pbf, work):
    """Squares that some road passes through (so sea squares are never extracted)."""
    import osmium
    roads = work / "roads.osm.pbf"
    run("osmium", "tags-filter", pbf, "w/highway", "-o", roads, "--overwrite")
    found = set()

    class Ways(osmium.SimpleHandler):
        def way(self, w):
            for n in w.nodes:
                if n.location.valid():
                    found.add((math.floor(n.location.lat * 4), math.floor(n.location.lon * 4)))

    Ways().apply_file(str(roads), locations=True)
    roads.unlink()
    return found



def _clean(value):
    """A tag value as one TSV field (OSM values are free text): tabs and line breaks become spaces."""
    return " ".join(str(value or "").split())[:255]


def entrances(pbf, work, bounds):
    """OSM entrance points inside bounds (S, W, N, E; south/west edges inclusive), sorted by node id."""
    import osmium
    s, w, n, e = bounds
    tagged = work / f"ent-{Path(pbf).stem}.osm.pbf"
    run("osmium", "tags-filter", pbf, "n/entrance", "n/amenity=parking_entrance", "-o", tagged, "--overwrite")
    rows = []

    class Nodes(osmium.SimpleHandler):
        def node(self, node):
            if not node.location.valid():
                return
            la, lo = node.location.lat, node.location.lon
            if not (s <= la < n and w <= lo < e):
                return
            t = node.tags
            amenity = t.get("amenity", "")
            if "entrance" not in t and amenity != "parking_entrance":
                return
            rows.append((node.id, round(la * 1e7), round(lo * 1e7), _clean(t.get("entrance", "")),
                         "parking_entrance" if amenity == "parking_entrance" else "", _clean(t.get("access", ""))))

    Nodes().apply_file(str(tagged))
    tagged.unlink()
    return sorted(rows)


def write_entrances(rows, dest, source):
    """Same rows, same bytes (no name or time in the gzip header)."""
    lines = ["\t".join((ENT_HEADER, "1", _clean(source), ATTRIBUTION)), "\t".join(ENT_COLUMNS)]
    lines += ["\t".join(map(str, r)) for r in rows]
    with open(dest, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as out:
        out.write(("\n".join(lines) + "\n").encode("utf-8"))


def require_uk_road_squares(squares):
    """Reject worldwide OSM change contamination before any UK graph is written.

    Deliberately broad UK envelope only. This cannot verify exact borders,
    Crown Dependencies, Northern Ireland clipping, or legal road restrictions.
    """
    if not squares:
        raise ValueError("DMN_UK_MAIN_ROADS_EMPTY")
    outside = sorted((y, x) for y, x in squares
                     if not (48.0 <= y / 4 <= 62.0 and -12.0 <= x / 4 <= 4.0))
    if outside:
        raise ValueError(f"DMN_UK_ENVELOPE_VIOLATION: {len(outside)} road squares outside "
                         f"broad UK bounds; first three quarter-degree indices {outside[:3]}")
    return squares


def band(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
    y0, y1 = (int(v) for v in a.rows.split(":"))
    south, north = y0 / 4 - MARGIN, (y1 + 1) / 4 + MARGIN
    band_pbf = work / f"band-{y0}.osm.pbf"
    run("osmium", "extract", "-b", f"{WEST - MARGIN},{south},{EAST + MARGIN},{north}", a.pbf,
        "-o", band_pbf, "--overwrite", "-s", "complete_ways")
    squares = sorted(road_squares(band_pbf, work) & {(y, x) for y in range(y0, y1 + 1)
                                                   for x in range(math.floor(WEST * 4), math.ceil(EAST * 4))})
    print(f"rows {y0}-{y1}: {len(squares)} squares with roads", flush=True)
    lock = threading.Lock()  # one extract at a time: each holds a few GB of id tables

    def one(sq):
        y, x = sq
        src = work / f"sq-{y}_{x}.osm.pbf"
        dest = out / f"dmn-{y}_{x}.dmnpack"
        with lock:
            run("osmium", "extract", "-b", f"{x / 4 - MARGIN},{y / 4 - MARGIN},{(x + 1) / 4 + MARGIN},{(y + 1) / 4 + MARGIN}",
                band_pbf, "-o", src, "--overwrite", "-s", "complete_ways")
        if src.stat().st_size <= MIN_BYTES:
            src.unlink()
            return None
        meta = pack(a.dmn, src, dest, ["--source", a.source, "--tile", f"{y / 4},{x / 4},{(y + 1) / 4},{(x + 1) / 4}"])
        if not meta or meta["edges"] == 0:
            src.unlink()
            dest.unlink(missing_ok=True)
            return None
        rows = entrances(src, work, (y / 4, x / 4, (y + 1) / 4, (x + 1) / 4))
        src.unlink()
        write_entrances(rows, out / f"dmn-ent-{y}_{x}.tsv.gz", a.source)
        meta["entrance_points"] = len(rows)
        return f"{y}_{x}", meta

    with concurrent.futures.ThreadPoolExecutor(a.jobs) as pool:
        done = [m for m in pool.map(one, squares) if m]
    band_pbf.unlink()
    (out / f"band-{y0}.json").write_text(json.dumps(dict(done), indent=1))
    print(f"rows {y0}-{y1}: {len(done)} pieces", flush=True)


def main_roads(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
    main_pbf = work / "main.osm.pbf"
    run("osmium", "tags-filter", a.pbf, "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link",
        "r/type=restriction", "-o", main_pbf, "--overwrite")
    # Inspect every source road square BEFORE writing the whole-main graph.
    # The global OSM hourly diff may contain non-UK nodes/ways.
    squares = require_uk_road_squares(road_squares(main_pbf, work))
    meta = pack(a.dmn, main_pbf, out / "dmn-main-uk.dmnpack", ["--source", a.source, "--main"])
    (out / "main.json").write_text(json.dumps(meta, indent=1))
    # The same main roads in 2-degree blocks, so a long trip loads only
    # the blocks along its way (the whole-UK piece is kept for older app versions)
    blocks = {(y // 8, x // 8) for y, x in squares}
    for by, bx in sorted(blocks):
        s, w = by * BLOCK, bx * BLOCK
        src = work / f"main-{by}_{bx}.osm.pbf"
        run("osmium", "extract", "-b", f"{w - MARGIN},{s - MARGIN},{w + BLOCK + MARGIN},{s + BLOCK + MARGIN}",
            main_pbf, "-o", src, "--overwrite", "-s", "complete_ways")
        dest = out / f"dmn-main-{by}_{bx}.dmnpack"
        part = pack(a.dmn, src, dest, ["--source", a.source, "--main", "--tile", f"{s},{w},{s + BLOCK},{w + BLOCK}"])
        src.unlink()
        if not part or part["edges"] == 0:
            dest.unlink(missing_ok=True)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def index(a):
    out = Path(a.out)
    tiles = {}
    regions = {}
    entrances = {}
    for f in sorted(out.glob("dmn-ent-*_*.tsv.gz")):
        key = f.name.removeprefix("dmn-ent-").removesuffix(".tsv.gz")
        entrances[key] = {"file": f.name, "sha256": sha(f), "bytes": f.stat().st_size}
    for f in sorted(out.glob("dmn-*_*.dmnpack")):
        key = f.stem.removeprefix("dmn-")
        (regions if key.startswith("main-") else tiles)[key] = {"file": f.name, "sha256": sha(f), "bytes": f.stat().st_size}
    main = out / "dmn-main-uk.dmnpack"
    if not tiles or not main.exists():
        raise SystemExit("missing pieces")
    doc = {"schema": 1, "format": "DMNPACK2", "built": a.built, "source": a.source, "release": a.release,
           "attribution": "© OpenStreetMap contributors, ODbL 1.0",
           "main": {"file": main.name, "sha256": sha(main), "bytes": main.stat().st_size},
           "tiles": tiles, "regions": regions}
    if entrances:
        # P15.2: one entrance file per road square (older apps ignore this key).
        stray = sorted(set(entrances) - set(tiles))
        if stray:
            raise SystemExit(f"entrance files without a road square: {stray[:3]}")
        doc["entrances"] = entrances
    if a.regions:
        # The regional catalogue these pieces answer to (dmn_due.py), for the twice-daily rebuild check.
        doc["catalog_regions"] = json.loads(Path(a.regions).read_text())
    (out / "dmn-uk.json").write_text(json.dumps(doc, indent=1, sort_keys=True))
    total = sum(t["bytes"] for t in tiles.values())
    print(f"{len(tiles)} squares, {total / 1e6:.0f} MB; main roads {doc['main']['bytes'] / 1e6:.0f} MB, "
          f"also in {len(regions)} blocks of {BLOCK} degrees")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("band", "main"):
        p = sub.add_parser(name)
        p.add_argument("pbf")
        p.add_argument("--dmn", required=True, help="drivemate-tests checkout (tools/dmn_pack.py)")
        p.add_argument("--out", required=True)
        p.add_argument("--work", default="work/dmn")
        p.add_argument("--source", default="DriveMate OSM snapshot")
        if name == "band":
            p.add_argument("--rows", required=True, help="first:last floor(lat*4) row")
            p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p = sub.add_parser("index")
    p.add_argument("out")
    p.add_argument("--source", default="DriveMate OSM snapshot")
    p.add_argument("--built", default="")
    p.add_argument("--release", default="dmn-uk", help="the dated release holding this build's pieces")
    p.add_argument("--regions", help="regions.json from dmn_due.py")
    a = ap.parse_args()
    {"band": band, "main": main_roads, "index": index}[a.cmd](a)


if __name__ == "__main__":
    main()
