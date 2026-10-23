"""DMN's UK road map in pieces for the DriveMate app (owner, 18 Oct 2026: "DMN can use the online
map, downloading and keeping the bits needed").

Squares of a quarter degree (dmn-<floor(lat*4)>_<floor(lon*4)>.dmnpack, every road) and one UK
main-roads piece (dmn-main-uk.dmnpack: motorways and trunk roads), all written by DMN's own map
reader (DriveMate-Navigation tools/dmn_pack.py) from DriveMate's own OSM snapshot. Each square is
read from an extract with a margin, so dead ends, barriers and turn bans at its edge are read as in
the whole map; the phone joins squares by OSM node id.

    python3 scripts/dmn_tiles.py band uk.osm.pbf --rows 199:204 --dmn DMN_DIR --out out/
    python3 scripts/dmn_tiles.py main uk.osm.pbf --dmn DMN_DIR --out out/
    python3 scripts/dmn_tiles.py index out/ --source TAG
"""
import argparse
import concurrent.futures
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
        src.unlink()
        if not meta or meta["edges"] == 0:
            dest.unlink(missing_ok=True)
            return None
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
    meta = pack(a.dmn, main_pbf, out / "dmn-main-uk.dmnpack", ["--source", a.source, "--main"])
    (out / "main.json").write_text(json.dumps(meta, indent=1))
    # Test 1.3 (owner, 18 Oct 2026): the same main roads in 2-degree blocks, so a long trip loads only
    # the blocks along its way (the whole-UK piece is kept for older app versions)
    blocks = {(y // 8, x // 8) for y, x in road_squares(main_pbf, work)}
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
        p.add_argument("--dmn", required=True, help="DriveMate-Navigation checkout")
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
    a = ap.parse_args()
    {"band": band, "main": main_roads, "index": index}[a.cmd](a)


if __name__ == "__main__":
    main()
