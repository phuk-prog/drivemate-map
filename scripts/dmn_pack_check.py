"""Checks DMN's UK road pieces (release "dmn-uk") as the phone will get them: test 1.2 part A.

Owner, 18 Oct 2026 (plan test 1.2: offline map package checked, damaged file refused, safe update
and rollback). Every piece the index lists must be there, the size and SHA-256 the index gives, and
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
    return {"built": doc.get("built"), "source": doc.get("source"), "pieces": len(entries),
            "refused": bad, "bytes": total, "edges": edges}


def gate(new: dict, live: dict | None) -> list[str]:
    """Reasons a new build must not replace the live one (empty: safe to publish). A week whose
    pieces don't all pass, or that loses squares or much of the map, is held back."""
    why = [f"refused {name}: {reason}" for name, reason in sorted(new["refused"].items())]
    if live:
        old_count = len(live.get("tiles", {})) + 1
        old_bytes = sum(t["bytes"] for t in live.get("tiles", {}).values()) + live.get("main", {}).get("bytes", 0)
        if new["pieces"] < 0.98 * old_count:
            why.append(f"{new['pieces']} pieces, live has {old_count}")
        if new["bytes"] < 0.9 * old_bytes:
            why.append(f"{new['bytes'] / 1e6:.0f} MB, live has {old_bytes / 1e6:.0f} MB")
    return why


def uploaded(doc: dict, assets: list[dict]) -> list[str]:
    """Pieces the index lists that are not in the release at their full size."""
    have = {a["name"]: a["size"] for a in assets}
    return [f"{p['file']}: {'missing' if p['file'] not in have else 'size ' + str(have[p['file']])}"
            for _, p in check_index(doc) if have.get(p["file"]) != p["bytes"]]


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
