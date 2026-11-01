import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("entrance_candidates", Path(__file__).parents[1] / "scripts/entrance_candidates.py")
entrances = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrances)

# A road running due east at latitude 53.0. At that latitude 0.001 deg of longitude is about 67 m.
ROAD = [[-2.1010, 53.0000], [-2.0990, 53.0000]]


def feature(kind, coordinates, tags, identity):
    return {"type": "Feature", "id": identity,
            "properties": {"@id": identity, "@type": identity.split("/")[0], **tags},
            "geometry": {"type": kind, "coordinates": coordinates}}


class EntranceCandidateTests(unittest.TestCase):
    def run_select(self, features):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "features.geojsonseq"
            out = Path(tmp) / "entrances.json"
            source.write_text("".join("\x1e" + json.dumps(f) + "\n" for f in features), encoding="utf-8")
            nodes, roads = entrances.load(source)
        return entrances.select(nodes, roads)

    def test_entrance_beside_a_drivable_road_is_kept_with_road_and_side(self):
        road = feature("LineString", ROAD, {"highway": "residential", "name": "Test Road"}, "way/1")
        # North of an eastbound road is on its left.
        node = feature("Point", [-2.1005, 53.00005], {"entrance": "main"}, "node/10")
        records, rejected = self.run_select([road, node])
        self.assertEqual(rejected, {})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["road"], "way/1")
        self.assertEqual(records[0]["side"], "left")
        self.assertLess(records[0]["road_m"], 10)

    def test_entrance_on_the_south_side_is_on_the_right(self):
        road = feature("LineString", ROAD, {"highway": "residential"}, "way/1")
        node = feature("Point", [-2.1005, 52.99995], {"entrance": "yes"}, "node/11")
        records, _ = self.run_select([road, node])
        self.assertEqual(records[0]["side"], "right")

    def test_entrance_with_no_drivable_road_nearby_is_rejected_not_guessed(self):
        footway = feature("LineString", ROAD, {"highway": "footway"}, "way/2")
        node = feature("Point", [-2.1005, 53.00005], {"entrance": "main"}, "node/12")
        records, rejected = self.run_select([footway, node])
        self.assertEqual(records, [])
        self.assertEqual(rejected, {"no_drivable_road_nearby": 1})

    def test_road_closed_to_motor_vehicles_does_not_count(self):
        road = feature("LineString", ROAD, {"highway": "residential", "motor_vehicle": "no"}, "way/3")
        node = feature("Point", [-2.1005, 53.00005], {"entrance": "main"}, "node/13")
        records, rejected = self.run_select([road, node])
        self.assertEqual(records, [])
        self.assertEqual(rejected, {"no_drivable_road_nearby": 1})

    def test_barred_and_emergency_entrances_are_excluded(self):
        road = feature("LineString", ROAD, {"highway": "residential"}, "way/1")
        barred = feature("Point", [-2.1005, 53.00005], {"entrance": "main", "access": "private"}, "node/14")
        emergency = feature("Point", [-2.1003, 53.00005], {"entrance": "emergency"}, "node/15")
        records, rejected = self.run_select([road, barred, emergency])
        self.assertEqual(records, [])
        self.assertEqual(rejected, {"entrance_barred": 1, "entrance_not_for_drivers": 1})

    def test_entrance_far_from_any_road_is_rejected(self):
        road = feature("LineString", ROAD, {"highway": "residential"}, "way/1")
        node = feature("Point", [-2.1005, 53.0020], {"entrance": "main"}, "node/16")  # ~220 m north
        records, rejected = self.run_select([road, node])
        self.assertEqual(records, [])
        self.assertEqual(rejected, {"no_drivable_road_nearby": 1})


if __name__ == "__main__":
    unittest.main()
