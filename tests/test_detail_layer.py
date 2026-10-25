"""Road furniture and signs from the OSM snapshot get the right kind and text."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import detail_layer as D  # noqa: E402


class Classify(unittest.TestCase):
    def test_kinds(self):
        cases = {
            "traffic lights": ({"highway": "traffic_signals"}, ("traffic_signals", "")),
            "stop": ({"highway": "stop"}, ("stop", "")),
            "give way": ({"highway": "give_way"}, ("give_way", "")),
            "camera": ({"highway": "speed_camera"}, ("speed_camera", "")),
            "level crossing": ({"railway": "level_crossing"}, ("level_crossing", "")),
            "speed bump": ({"traffic_calming": "bump"}, ("traffic_calming", "")),
            "bollard": ({"barrier": "bollard"}, ("barrier", "")),
            "UK 30 sign": ({"traffic_sign": "GB:670[30]"}, ("speed_limit_sign", "30")),
            "maxspeed sign": ({"traffic_sign": "maxspeed", "maxspeed": "20 mph"}, ("speed_limit_sign", "20")),
            "junction number": ({"highway": "motorway_junction", "ref": "27"}, ("motorway_junction", "27")),
            "other sign": ({"traffic_sign": "GB:616"}, ("road_sign", "GB:616")),
        }
        for name, (tags, want) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(D.classify(tags), want)

    def test_nothing_else(self):
        for tags in ({}, {"amenity": "bench"}, {"barrier": "kerb"}, {"highway": "street_lamp"}):
            with self.subTest(tags=tags):
                self.assertIsNone(D.classify(tags))


if __name__ == "__main__":
    unittest.main()
