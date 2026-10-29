"""Good DMN road pieces pass, damaged ones are refused."""
import copy
import gzip
import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dmn_pack_check as C  # noqa: E402


def _s(text):
    b = text.encode()
    return struct.pack("<H", len(b)) + b


def pack(tile=(53.25, -2.25, 53.5, -2.0), edge=(0, 1), meta_extra=None, tail=b""):
    """A small valid DMNPACK2 piece: two nodes, one way, one edge each way, one ban."""
    parts = [b"DMNPACK2", struct.pack("<I", 2),
             struct.pack("<iiq", 534000000, -21000000, 101), struct.pack("<iiq", 534010000, -21000000, 102),
             struct.pack("<I", 1), struct.pack("<qB", 7, 0) + _s("High Street") + _s("") + _s("residential"),
             struct.pack("<I", 2),
             struct.pack("<iiffBBBBiHB", edge[0], edge[1], 111.0, 32.0, 0, 0, 0, 0, 0, 0, 0),
             struct.pack("<iiffBBBBiHB", 1, 0, 111.0, 32.0, 0, 0, 0, 0, 0, 0, 1),
             struct.pack("<I", 1), struct.pack("<ii", 0, 1),
             struct.pack("<I", 0), struct.pack("<I", 1), struct.pack("<i", 0)]
    meta = {"format": "DMNPACK2", "tile": list(tile) if tile else None, "main_roads": tile is None,
            "attribution": C.ATTRIBUTION, "nodes": 2, "ways": 1, "edges": 2}
    meta.update(meta_extra or {})
    parts.append(_s(json.dumps(meta)))
    return gzip.compress(b"".join(parts) + tail, mtime=0)


def entry(data, name):
    return {"file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def index(tile_data, main_data):
    return {"schema": 1, "format": "DMNPACK2", "attribution": C.ATTRIBUTION, "built": "2026-10-18",
            "source": "test", "tiles": {"213_-9": entry(tile_data, "dmn-213_-9.dmnpack")},
            "main": entry(main_data, "dmn-main-uk.dmnpack")}


class GoodPieces(unittest.TestCase):
    def test_good_square_and_main_pass(self):
        t, m = pack(), pack(tile=None)
        entries = dict(C.check_index(index(t, m)))
        self.assertEqual(C.check_piece("213_-9", entries["213_-9"], t)["edges"], 2)
        self.assertTrue(C.check_piece("main", entries["main"], m)["main_roads"])

    def test_folder_check(self):
        t, m = pack(), pack(tile=None)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "dmn-uk.json").write_text(json.dumps(index(t, m)))
            (Path(d) / "dmn-213_-9.dmnpack").write_bytes(t)
            (Path(d) / "dmn-main-uk.dmnpack").write_bytes(m)
            out = C.check_dir(Path(d))
            self.assertEqual((out["pieces"], out["refused"]), (2, {}))
            (Path(d) / "dmn-213_-9.dmnpack").write_bytes(t[:-10])
            (Path(d) / "dmn-main-uk.dmnpack").unlink()
            self.assertEqual(set(C.check_dir(Path(d))["refused"]), {"dmn-213_-9.dmnpack", "dmn-main-uk.dmnpack"})


class DamagedPiecesRefused(unittest.TestCase):
    def refused(self, data, key="213_-9", ent=None):
        ent = ent or entry(data, "dmn-213_-9.dmnpack")
        with self.assertRaises(C.Refused):
            C.check_piece(key, ent, data)

    def test_cut_short(self):
        good = pack()
        for n in (1, 10, len(good) // 2, len(good) - 1):
            with self.subTest(cut=n):
                self.refused(good[:-n])

    def test_flipped_byte(self):
        good = pack()
        for at in (12, len(good) // 2, len(good) - 6):
            with self.subTest(at=at):
                bad = bytearray(good); bad[at] ^= 0x40
                self.refused(bytes(bad))

    def test_checksum_or_size_not_the_index(self):
        good, other = pack(), pack(tile=(53.25, -2.25, 53.5, -2.0), meta_extra={"source": "other week"})
        self.refused(other, ent=entry(good, "dmn-213_-9.dmnpack"))
        self.refused(good, ent={**entry(good, "dmn-213_-9.dmnpack"), "bytes": len(good) + 5})

    def test_broken_insides(self):
        cases = {"not gzip": b"DMNPACK2" + b"\0" * 40,
                 "old layout": gzip.compress(b"DMNPACK1" + b"\0" * 40),
                 "edge to a missing node": pack(edge=(0, 9)),
                 "bytes after the end": pack(tail=b"x"),
                 "counts disagree": pack(meta_extra={"edges": 3}),
                 "wrong square": pack(tile=(53.5, -2.25, 53.75, -2.0)),
                 "gzip plus junk": pack() + b"junk"}
        for why, data in cases.items():
            with self.subTest(why=why):
                self.refused(data)

    def test_square_piece_as_main_refused(self):
        t = pack()
        self.refused(t, key="main", ent=entry(t, "dmn-main-uk.dmnpack"))


class BadIndexRefused(unittest.TestCase):
    def test_each_fault(self):
        good = index(pack(), pack(tile=None))
        C.check_index(good)
        faults = {
            "schema 2": lambda d: d.update(schema=2),
            "other format": lambda d: d.update(format="DMNPACK1"),
            "no squares": lambda d: d.update(tiles={}),
            "no main roads": lambda d: d.pop("main"),
            "path in name": lambda d: d["tiles"]["213_-9"].update(file="../dmn-213_-9.dmnpack"),
            "name not its square": lambda d: d["tiles"]["213_-9"].update(file="dmn-214_-9.dmnpack"),
            "bad key": lambda d: d["tiles"].update({"x": d["tiles"]["213_-9"]}),
            "short checksum": lambda d: d["main"].update(sha256="abc"),
            "too big": lambda d: d["main"].update(bytes=C.MAX_BYTES + 1),
            "empty": lambda d: d["main"].update(bytes=0),
            "listed twice": lambda d: d["main"].update(file="dmn-213_-9.dmnpack"),
            "release outside dmn-uk": lambda d: d.update(release="../other"),
        }
        for why, damage in faults.items():
            with self.subTest(why=why):
                d = copy.deepcopy(good); damage(d)
                with self.assertRaises(C.Refused):
                    C.check_index(d)


class MainRoadBlocks(unittest.TestCase):
    def test_block_pieces_checked(self):
        block = pack(tile=(52.0, -4.0, 54.0, -2.0), meta_extra={"main_roads": True})
        d = index(pack(), pack(tile=None)); d["regions"] = {"main-26_-2": entry(block, "dmn-main-26_-2.dmnpack")}
        entries = dict(C.check_index(d))
        C.check_piece("main-26_-2", entries["main-26_-2"], block)
        wrong = pack(tile=(54.0, -4.0, 56.0, -2.0), meta_extra={"main_roads": True})
        with self.assertRaises(C.Refused):
            C.check_piece("main-26_-2", entry(wrong, "dmn-main-26_-2.dmnpack"), wrong)
        d["regions"] = {"main-x": entry(block, "dmn-main-x.dmnpack")}
        with self.assertRaises(C.Refused):
            C.check_index(d)


class SafePublish(unittest.TestCase):
    def test_gate_holds_back_a_bad_or_shrunken_week(self):
        live = index(pack(), pack(tile=None))
        good = {"pieces": 2, "refused": {}, "bytes": live["tiles"]["213_-9"]["bytes"] + live["main"]["bytes"]}
        self.assertEqual(C.gate(good, live), [])
        self.assertEqual(C.gate(good, None), [])
        self.assertTrue(C.gate({**good, "refused": {"dmn-213_-9.dmnpack": "cut short"}}, live))
        self.assertTrue(C.gate({**good, "pieces": 1}, live))
        self.assertTrue(C.gate({**good, "bytes": good["bytes"] // 2}, live))

    def test_uploaded_finds_missing_or_partial_pieces(self):
        doc = index(pack(), pack(tile=None))
        full = [{"name": p["file"], "size": p["bytes"]} for p in (doc["tiles"]["213_-9"], doc["main"])]
        self.assertEqual(C.uploaded(doc, full), [])
        self.assertEqual(len(C.uploaded(doc, full[:1])), 1)
        self.assertEqual(len(C.uploaded(doc, [full[0], {**full[1], "size": 3}])), 1)

    def test_dated_release_accepted(self):
        d = index(pack(), pack(tile=None)); d["release"] = "dmn-uk-20261018-12"
        C.check_index(d)


if __name__ == "__main__":
    unittest.main()
