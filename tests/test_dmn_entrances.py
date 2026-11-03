"""P15.2: each road square's OSM entrance points are published as tagged, checked, uploaded and indexed."""
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dmn_pack_check as C  # noqa: E402
import dmn_tiles as T  # noqa: E402
import dmn_upload as U  # noqa: E402

SQUARE = (53.25, -2.25, 53.5, -2.0)  # square 213_-9 (Stockport)
OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
 <node id="5" version="1" lat="53.4000000" lon="-2.1500000"><tag k="entrance" v="main"/></node>
 <node id="3" version="1" lat="53.4001000" lon="-2.1501000"><tag k="entrance" v="service"/><tag k="access" v="private"/></node>
 <node id="7" version="1" lat="53.4100000" lon="-2.1600000"><tag k="amenity" v="parking_entrance"/></node>
 <node id="8" version="1" lat="53.4100000" lon="-2.1700000"><tag k="amenity" v="parking"/></node>
 <node id="9" version="1" lat="53.5100000" lon="-2.1500000"><tag k="entrance" v="yes"/></node>
 <node id="10" version="1" lat="53.2500000" lon="-2.2500000"><tag k="entrance" v="yes"/><tag k="access" v="a\tb"/></node>
 <node id="11" version="1" lat="53.4000000" lon="-2.1400000"><tag k="building" v="house"/></node>
</osm>
"""


@unittest.skipUnless(shutil.which("osmium"), "osmium-tool is needed (the workflow installs it)")
class EntranceFiles(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        src = self.dir / "in.osm"
        src.write_text(OSM)
        self.pbf = self.dir / "in.osm.pbf"
        subprocess.run(["osmium", "cat", src, "-o", self.pbf], check=True)

    def build(self):
        rows = T.entrances(self.pbf, self.dir, SQUARE)
        dest = self.dir / "dmn-ent-213_-9.tsv.gz"
        T.write_entrances(rows, dest, "test snapshot")
        return rows, dest

    def test_only_tagged_entrances_inside_the_square_in_node_order_with_tags_unchanged(self):
        rows, _ = self.build()
        self.assertEqual(rows, [
            (3, 534001000, -21501000, "service", "", "private"),
            (5, 534000000, -21500000, "main", "", ""),
            (7, 534100000, -21600000, "", "parking_entrance", ""),
            (10, 532500000, -22500000, "yes", "", "a b"),  # south-west corner belongs here; tab made a space
        ])

    def test_same_points_give_the_same_bytes_and_pass_the_check(self):
        rows, dest = self.build()
        first = dest.read_bytes()
        T.write_entrances(rows, dest, "test snapshot")
        self.assertEqual(dest.read_bytes(), first)
        entry = {"file": dest.name, "sha256": hashlib.sha256(first).hexdigest(), "bytes": len(first)}
        self.assertEqual(C.check_entrances("213_-9", entry, first), 4)
        with self.assertRaises(C.Refused):  # the wrong square
            C.check_entrances("213_-8", entry, first)

    def test_damaged_or_cut_off_files_are_refused(self):
        _, dest = self.build()
        text = gzip.decompress(dest.read_bytes()).decode()
        for bad in (text[:-1],  # cut off mid-row
                    text.replace("main", "main\textra"),
                    text.replace("5\t534000000", "2\t534000000"),  # out of node order
                    text.replace("\tparking_entrance", "\tparking"),
                    text.replace("ODbL 1.0", "")):
            data = gzip.compress(bad.encode(), mtime=0)
            entry = {"file": dest.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            with self.assertRaises(C.Refused, msg=bad[:80]):
                C.check_entrances("213_-9", entry, data)


class EntranceIndex(unittest.TestCase):
    def test_index_lists_entrances_by_square_and_upload_check_includes_them(self):
        doc = {"tiles": {"213_-9": {}}, "entrances": {"213_-9": {"file": "dmn-ent-213_-9.tsv.gz", "sha256": "a" * 64, "bytes": 9}}}
        self.assertEqual([k for k, _ in C.entrance_entries(doc)], ["213_-9"])
        self.assertEqual(C.entrance_entries({"tiles": {}}), [])  # an older index has none
        for bad in ({"213_-8": {"file": "dmn-ent-213_-8.tsv.gz", "sha256": "a" * 64, "bytes": 9}},
                    {"213_-9": {"file": "dmn-213_-9.dmnpack", "sha256": "a" * 64, "bytes": 9}}):
            with self.assertRaises(C.Refused):
                C.entrance_entries({"tiles": {"213_-9": {}}, "entrances": bad})

    def test_upload_accepts_entrance_files_and_nothing_else_new(self):
        self.assertTrue(U.PIECE.fullmatch("dmn-ent-213_-9.tsv.gz"))
        self.assertTrue(U.PIECE.fullmatch("dmn-213_-9.dmnpack"))
        self.assertFalse(U.PIECE.fullmatch("dmn-ent-213_-9.tsv"))
        self.assertFalse(U.PIECE.fullmatch("other.tsv.gz"))

    def test_workflow_uploads_entrance_files(self):
        wf = (Path(__file__).resolve().parents[1] / ".github/workflows/dmn-pack.yml").read_text()
        self.assertIn("parts/dmn-ent-*.tsv.gz", wf)


if __name__ == "__main__":
    unittest.main()
