"""Checks the derived Yew Street / Edgeley fixture: schema, attribution and the no-invented-exit rule."""
import json
from pathlib import Path
import unittest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "uk-yew-street-edgeley.json"


class YewStreetFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_source_is_attributed_and_hashed(self):
        source = self.data["source"]
        self.assertIn("OpenStreetMap", source["attribution"])
        self.assertRegex(source["raw_extract_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(source["bbox_lon_lat"]), 4)

    def test_coverage_matches_content(self):
        cov = self.data["coverage"]
        self.assertEqual(cov["roundabout_count"], len(self.data["roundabouts"]))
        self.assertEqual(cov["approach_way_count"], len(self.data["approach_ways"]))

    def test_no_exit_count_is_invented(self):
        # OSM carries no exit tags here, so every roundabout must report its exit count as unmapped.
        for roundabout in self.data["roundabouts"]:
            self.assertFalse(roundabout["exit_count_mapped"], roundabout["id"])
        for entry in self.data["approach_ways"]:
            self.assertEqual(entry["way"]["exit_tags"], {}, entry["way"]["id"])

    def test_approaches_join_their_ring(self):
        ring = {n for r in self.data["roundabouts"] for n in r["node_ids"]}
        for entry in self.data["approach_ways"]:
            self.assertTrue(entry["joins_ring_at_nodes"])
            self.assertTrue(set(entry["joins_ring_at_nodes"]) <= ring, entry["way"]["id"])

    def test_split_ring_pieces_are_recorded_not_closed(self):
        # The Armoury Roundabout (B5465) is stored as unclosed pieces; the fixture must say so.
        armoury = [r for r in self.data["roundabouts"] if r["name"] == "Armoury Roundabout"]
        self.assertEqual(len(armoury), 7)
        self.assertTrue(all(r["closed"] is False for r in armoury))


if __name__ == "__main__":
    unittest.main()
