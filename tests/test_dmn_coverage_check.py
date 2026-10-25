"""Missing squares and places without roads are found."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dmn_coverage_check as C  # noqa: E402
from test_dmn_pack_check import pack  # noqa: E402  (a small valid piece, nodes at 53.40/53.401, -2.10)


class Coverage(unittest.TestCase):
    def setUp(self):
        self.places = C.PLACES

    def tearDown(self):
        C.PLACES = self.places

    def folder(self, d, keys):
        tiles = {k: {"file": f"dmn-{k}.dmnpack"} for k in keys}
        (Path(d) / "dmn-uk.json").write_text(json.dumps({"tiles": tiles}))
        for k in keys:
            (Path(d) / f"dmn-{k}.dmnpack").write_bytes(pack())

    def test_missing_square_with_roads_fails_slivers_ignored(self):
        C.PLACES = {"islands": []}
        with tempfile.TemporaryDirectory() as d:
            self.folder(d, ["213_-9"])
            lines, fails = C.check(Path(d), {"213_-9": 900, "214_-9": 40, "215_-9": 2})
            self.assertEqual(fails, ["square 214_-9 (40 road points in OpenStreetMap) has no piece"])
            self.assertIn("1/2 squares", lines[0])

    def test_place_needs_its_piece_and_a_road_nearby(self):
        C.PLACES = {"islands": [("Near", 53.405, -2.10), ("Far", 53.48, -2.24), ("No piece", 55.0, -2.0)]}
        with tempfile.TemporaryDirectory() as d:
            self.folder(d, ["213_-9"])
            lines, fails = C.check(Path(d), None)
            self.assertEqual(lines, ["- FAIL islands: 1/3 have roads"])
            self.assertTrue(fails[0].startswith("Far: no road within 2 km"))
            self.assertEqual(fails[1], "No piece: square 220_-8 has no piece")

    def test_keys_and_distance(self):
        self.assertEqual(C.key(53.408, -2.158), "213_-9")
        self.assertAlmostEqual(C.metres((53.0, -2.0), (53.009, -2.0)), 1000, delta=5)


if __name__ == "__main__":
    unittest.main()
