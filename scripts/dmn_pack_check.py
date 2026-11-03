"""Checks DMN's UK road pieces (release "dmn-uk") as the phone will get them.

Offline map package checked, damaged file refused, safe update and rollback. Every piece the index lists must be there, the size and SHA-256 the index gives, and
open completely with a strict reader of the DMNPACK2 layout (DriveMate-Navigation tools/dmn_pack.py;
the app's reader is app/.../dmn/DmnPack.kt): every count, index and code inside its range, no bytes
left over, and metadata saying the right square and the OpenStreetMap credit. Anything else is
refused with the reason.

    python3 scripts/dmn_pack_check.py check DIR        # DIR holds dmn-uk.json and the pieces
    python3 scripts/dmn_pack_check.py gate DIR --live live.json   # before publishing: no worse than live
    python3 scripts/dmn_pack_check.py uploaded DIR/dmn-uk.json assets.json   # every piece uploaded whole
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import struct
import sys
import zlib
from pathlib import Path

ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
FILE_NAME = re.compile(r"^dmn-[A-Za-z0-9_-]+\.dmnpack$")  # the app's own rule (DmnTiles.parse)
TILE_KEY = re.compile(r"^(-?\d+)_(-?\d+)$")
REGION_KEY = re.compile(r"^main-(-?\d+)_(-?\d+)$")  # main roads in 2-degree blocks
BLOCK = 2
SHA = re.compile(r"^[0-9a-f]{64}$")
ENT_FILE = re.compile(r"^dmn-ent-(-?\d+)_(-?\d+)\.tsv\.gz$")
ENT_COLUMNS = ["osm_node", "lat_e7", "lon_e7", "entrance", "amenity", "access"]
MAX_ENT_BYTES = 50 * 1024 * 1024
RELEASE = re.compile(r"^dmn-uk(-[0-9]{8}(-[0-9]+)?)?$")  # the app's own rule (DmnTiles.parse)
MAX_BYTES = 200 * 1024 * 1024  # the app's limit for one piece (DmnTiles.MAX_BYTES)
MAX_UNPACKED = 2 * 1024 ** 3


class Refused(ValueError):
    """A piece or index that must not be used, with the reason."""


class _Reader:
    def __init__(self, raw: bytes):
        self.raw, self.at = raw, 0

    def take(self, fmt: str):
        size = struct.calcsize(fmt)
        if self.at + size > len(self.raw):
            raise Refused("cut short")
        out = struct.unpack_from(fmt, self.raw, self.at)
        self.at += size
        return out

    def count(self, each: int) -> int:
        (n,) = self.take("<I")
        if n * each > len(self.raw) - self.at:
            raise Refused("count larger than the file")
        return n

    def text(self) -> str:
        (n,) = self.take("<H")
        if self.at + n > len(self.raw):
            raise Refused("cut short")
        try:
            out = self.raw[self.at:self.at + n].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Refused("text is not UTF-8") from exc
        self.at += n
        return out


def read_pack(data: bytes) -> dict:
    """The metadata of one complete, well-formed DMNPACK2 piece; Refused otherwise."""
    try:
        d = zlib.decompressobj(wbits=31)
        raw = d.decompress(data, MAX_UNPACKED)
        if d.unconsumed_tail:
            raise Refused("unpacks to more than 2 GB")
        if not d.eof:
            raise Refused("gzip stream cut short")
        if d.unused_data:
            raise Refused("bytes after the gzip stream")
    except zlib.error as exc:
        raise Refused(f"not a readable gzip file ({exc})") from exc
    r = _Reader(raw)
    if raw[:8] != b"DMNPACK2":
        raise Refused("not a DMNPACK2 road pack")
    r.at = 8
    nodes = r.count(16)
    for _ in range(nodes):
        lat, lon, _ = r.take("<iiq")
        if not (-900_000_000 <= lat <= 900_000_000 and -1_800_000_000 <= lon <= 1_800_000_000):
            raise Refused("node outside the Earth")
    ways = r.count(9 + 6)
    for _ in range(ways):
        r.take("<qB")
        r.text(), r.text(), r.text()
    edges = r.count(27)
    for _ in range(edges):
        a, b, metres, kph, acc, start, finish, flags, way, _, direction = r.take("<iiffBBBBiHB")
        if not (0 <= a < nodes and 0 <= b < nodes and 0 <= way < ways):
            raise Refused("edge points outside the pack")
        if max(acc, start, finish) > 2 or direction > 1 or flags > 1:
            raise Refused("unknown access or direction code")
        if not (metres > 0 and kph > 0) or metres != metres or kph != kph:
            raise Refused("edge with no length or speed")
    for _ in range(r.count(8)):
        if not all(0 <= x < edges for x in r.take("<ii")):
            raise Refused("turn ban points outside the pack")
    for _ in range(r.count(5)):
        (n,) = r.take("<B")
        if not all(0 <= x < edges for x in r.take(f"<{n}i")):
            raise Refused("banned sequence points outside the pack")
    for _ in range(r.count(4)):
        (n,) = r.take("<i")
        if not 0 <= n < nodes:
            raise Refused("U-turn node outside the pack")
    try:
        meta = json.loads(r.text())
    except json.JSONDecodeError as exc:
        raise Refused("metadata is not JSON") from exc
    if r.at != len(raw):
        raise Refused("bytes after the metadata")
    if meta.get("format") != "DMNPACK2" or meta.get("attribution") != ATTRIBUTION:
        raise Refused("metadata lacks the format or the OpenStreetMap credit")
    if (meta.get("nodes"), meta.get("ways"), meta.get("edges")) != (nodes, ways, edges):
        raise Refused("metadata counts differ from the pack")
    return meta


def check_index(doc: dict) -> list[tuple[str, dict]]:
    """(square key or "main", entry) for every piece of a well-formed index; Refused otherwise."""
    if not isinstance(doc, dict) or doc.get("schema") != 1 or doc.get("format") != "DMNPACK2":
        raise Refused("index is not schema 1 / DMNPACK2")
    if doc.get("attribution") != ATTRIBUTION:
        raise Refused("index lacks the OpenStreetMap credit")
    if not RELEASE.match(str(doc.get("release", "dmn-uk"))):
        raise Refused("index names a release the app would refuse")
    tiles, main = doc.get("tiles"), doc.get("main")
    if not isinstance(tiles, dict) or not tiles or not isinstance(main, dict):
        raise Refused("index has no squares or no main-roads piece")
    regions = doc.get("regions", {})
    if not isinstance(regions, dict):
        raise Refused("index regions are not a list of pieces")
    out = [(k, v) for k, v in sorted(tiles.items())] + [("main", main)] + sorted(regions.items())
    seen = set()
    for key, p in out:
        if key != "main" and not TILE_KEY.match(key) and not (key in regions and REGION_KEY.match(key)):
            raise Refused(f"bad square key {key!r}")
        if not isinstance(p, dict) or not FILE_NAME.match(str(p.get("file", ""))):
            raise Refused(f"{key}: bad file name")
        if key != "main" and p["file"] != f"dmn-{key}.dmnpack":
            raise Refused(f"{key}: file name {p['file']} does not match its square")
        if not SHA.match(str(p.get("sha256", ""))):
            raise Refused(f"{key}: bad SHA-256")
        if not isinstance(p.get("bytes"), int) or not 1 <= p["bytes"] <= MAX_BYTES:
            raise Refused(f"{key}: size outside 1 byte to 200 MB")
        if p["file"] in seen:
            raise Refused(f"{key}: file listed twice")
        seen.add(p["file"])
    return out


def entrance_entries(doc: dict) -> list[tuple[str, dict]]:
    """P15.2 entrance files the index lists (none for an older index); Refused when malformed."""
    ent = doc.get("entrances", {})
    if not isinstance(ent, dict):
        raise Refused("index entrances are not a list of files")
    tiles = doc.get("tiles") or {}
    for key, p in sorted(ent.items()):
        if key not in tiles:
            raise Refused(f"entrances {key}: no road square")
        if not isinstance(p, dict) or str(p.get("file", "")) != f"dmn-ent-{key}.tsv.gz":
            raise Refused(f"entrances {key}: bad file name")
        if not SHA.match(str(p.get("sha256", ""))):
            raise Refused(f"entrances {key}: bad SHA-256")
        if not isinstance(p.get("bytes"), int) or not 1 <= p["bytes"] <= MAX_ENT_BYTES:
            raise Refused(f"entrances {key}: size outside 1 byte to 50 MB")
    return sorted(ent.items())


def check_entrances(key: str, entry: dict, data: bytes) -> int:
    """Number of entrance points in a file that matches its entry and its square; Refused otherwise."""
    if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise Refused("size or SHA-256 differs from the index")
    try:
        lines = gzip.decompress(data).decode("utf-8").split("\n")
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise Refused(f"unreadable: {exc}") from None
    head = lines[0].split("\t")
    if head[:2] != ["#drivemate-entrances", "1"] or len(head) != 4 or head[3] != ATTRIBUTION:
        raise Refused("not a version-1 entrance file with the OpenStreetMap credit")
    if lines[1].split("\t") != ENT_COLUMNS or lines[-1] != "":
        raise Refused("bad columns or a cut-off file")
    row, col = map(int, TILE_KEY.match(key).groups())
    last = None
    for line in lines[2:-1]:
        f = line.split("\t")
        try:
            node, la, lo = int(f[0]), int(f[1]) / 1e7, int(f[2]) / 1e7
        except (ValueError, IndexError):
            raise Refused("bad entrance row") from None
        if len(f) != 6 or not (row <= la * 4 < row + 1 and col <= lo * 4 < col + 1):
            raise Refused(f"entrance {f[0]} outside square {key}")
        if not f[3] and f[4] != "parking_entrance" or f[4] not in ("", "parking_entrance"):
            raise Refused(f"entrance {node} carries no entrance tag")
        if last is not None and node <= last:
            raise Refused("entrance rows not in node order")
        last = node
    return len(lines) - 3


def check_piece(key: str, entry: dict, data: bytes) -> dict:
    """Metadata of a piece that matches its index entry and reads completely; Refused otherwise."""
    if len(data) != entry["bytes"]:
        raise Refused(f"size {len(data)} is not the index's {entry['bytes']}")
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise Refused("SHA-256 differs from the index")
    meta = read_pack(data)
    if key == "main" or REGION_KEY.match(key):
        if not meta.get("main_roads"):
            raise Refused("main-roads piece not marked as main roads")
        if key != "main":
            by, bx = map(int, REGION_KEY.match(key).groups())
            if [round(x) for x in (meta.get("tile") or [])] != [by * BLOCK, bx * BLOCK, (by + 1) * BLOCK, (bx + 1) * BLOCK]:
                raise Refused(f"metadata block {meta.get('tile')} is not block {key}")
    else:
        row, col = map(int, TILE_KEY.match(key).groups())
        tile = meta.get("tile") or []
        if [round(x * 4) for x in tile] != [row, col, row + 1, col + 1]:
            raise Refused(f"metadata square {tile} is not square {key}")
    return meta


def check_dir(folder: Path) -> dict:
    doc = json.loads((folder / "dmn-uk.json").read_text())
    entries = check_index(doc)
    bad, total, edges = {}, 0, 0
    for key, entry in entries:
        f = folder / entry["file"]
        try:
            if not f.exists():
                raise Refused("missing")
            meta = check_piece(key, entry, f.read_bytes())
            total += entry["bytes"]
            edges += meta["edges"]
        except Refused as exc:
            bad[entry["file"]] = str(exc)
    points = 0
    for key, entry in entrance_entries(doc):
        f = folder / entry["file"]
        try:
            if not f.exists():
                raise Refused("missing")
            points += check_entrances(key, entry, f.read_bytes())
        except Refused as exc:
            bad[entry["file"]] = str(exc)
    # Keep actual inventory separate from aggregate counts: a disappearing
    # UK square can otherwise be masked by new main-road block files.
    tile_entries = doc["tiles"]
    national_entries = list(tile_entries.values()) + [doc["main"]]
    return {"built": doc.get("built"), "source": doc.get("source"), "pieces": len(entries),
            "refused": bad, "bytes": total, "edges": edges, "entrance_points": points,
            "tile_keys": sorted(tile_entries),
            "main_block_keys": sorted(doc.get("regions", {})),
            "national_bytes": sum(p["bytes"] for p in national_entries)}


# Known road-bearing city-centre quarters in each UK nation. These are
# coverage sentinels, NOT verification of every road or its legal restrictions.
NATION_SAMPLES = {
    "England/Stockport": "213_-9",
    "Scotland/Edinburgh": "223_-13",
    "Wales/Cardiff": "205_-13",
    "Northern Ireland/Belfast": "218_-24",
}


def gate(new: dict, live: dict | None) -> list[str]:
    """Reasons a new build must not replace the live one (empty: safe to publish). A week whose
    pieces don't all pass, or that loses squares or much of the map, is held back."""
    why = [f"refused {name}: {reason}" for name, reason in sorted(new["refused"].items())]
    # Only check national provenance on an actual checked directory receipt.
    # Tests using abstract count-only summaries cannot claim national coverage.
    current_tiles = set(new["tile_keys"]) if "tile_keys" in new else None
    if current_tiles is not None:
        for nation, key in NATION_SAMPLES.items():
            if key not in current_tiles:
                why.append(f"missing {nation} road quarter {key}")
    if live:
        old_tiles = set(live.get("tiles", {}))
        old_blocks = set(live.get("regions", {}))
        old_count = len(old_tiles) + 1
        old_bytes = sum(t["bytes"] for t in live.get("tiles", {}).values()) + live.get("main", {}).get("bytes", 0)
        # Use tiles+main on *both* sides; adding regional blocks must never
        # hide loss of a whole country or a smaller road tile.
        national_count = len(current_tiles) + 1 if current_tiles is not None else new["pieces"]
        national_bytes = new.get("national_bytes", new["bytes"])
        if national_count < 0.98 * old_count:
            why.append(f"{national_count} tiles/main pieces, live has {old_count}")
        if national_bytes < 0.9 * old_bytes:
            why.append(f"{national_bytes / 1e6:.0f} MB of tiles/main roads, live has {old_bytes / 1e6:.0f} MB")
        if current_tiles is not None:
            missing_tiles = sorted(old_tiles - current_tiles)
            if missing_tiles:
                why.append(f"{len(missing_tiles)} live road quarters missing, first {missing_tiles[:8]}")
            missing_blocks = sorted(old_blocks - set(new.get("main_block_keys", [])))
            if missing_blocks:
                why.append(f"{len(missing_blocks)} live main-road blocks missing, first {missing_blocks[:8]}")
    return why


def uploaded(doc: dict, assets: list[dict]) -> list[str]:
    """Pieces the index lists that are not in the release at their full size."""
    have = {a["name"]: a["size"] for a in assets}
    return [f"{p['file']}: {'missing' if p['file'] not in have else 'size ' + str(have[p['file']])}"
            for _, p in check_index(doc) + entrance_entries(doc) if have.get(p["file"]) != p["bytes"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").add_argument("dir")
    g = sub.add_parser("gate")
    g.add_argument("dir")
    g.add_argument("--live", help="the index phones use now (none on a first publish)")
    u = sub.add_parser("uploaded")
    u.add_argument("index")
    u.add_argument("assets", help="JSON list of the release's assets (name, size)")
    a = ap.parse_args()
    if a.cmd == "uploaded":
        missing = uploaded(json.loads(Path(a.index).read_text()), json.loads(Path(a.assets).read_text()))
        print("\n".join(missing) or "every piece uploaded whole")
        return 1 if missing else 0
    try:
        out = check_dir(Path(a.dir))
    except (Refused, OSError, json.JSONDecodeError) as exc:
        print(f"Index refused: {exc}")
        return 1
    ok = out["pieces"] - len(out["refused"])
    print(f"## DMN road pieces: {ok}/{out['pieces']} pass (built {out['built']}, {out['source']})\n")
    print(f"{out['bytes'] / 1e6:.0f} MB, {out['edges']:,} road pieces in all.")
    if a.cmd == "check":
        for name, why in sorted(out["refused"].items()):
            print(f"- refused {name}: {why}")
        return 1 if out["refused"] else 0
    live = json.loads(Path(a.live).read_text()) if a.live and Path(a.live).exists() else None
    held = gate(out, live)
    print("\n".join(f"- held back: {w}" for w in held) or "Safe to publish.")
    return 1 if held else 0


if __name__ == "__main__":
    sys.exit(main())
