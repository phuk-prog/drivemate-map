"""Compare what the style can display, rather than hidden overview labels or lines."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import layout_compare as C


class VisibleLayoutComparison(unittest.TestCase):
    def test_poi_names_are_compared_at_the_same_source_zoom(self):
        old = {'name': 'Hospital', 'subclass': 'hospital'}
        own = {'name': 'Hospital', 'kind': 'hospital'}
        self.assertIsNone(C.name_group('poi', old, 12))
        self.assertIsNone(C.name_group('pois', own, 12))
        self.assertEqual('poi', C.name_group('poi', old, 14))
        self.assertEqual('poi', C.name_group('pois', own, 14))

    def test_busways_and_construction_are_compared_from_their_drawn_zoom(self):
        self.assertFalse(C.road_visible('construction', 12))
        self.assertFalse(C.road_visible('busway', 12))
        self.assertTrue(C.road_visible('construction', 14))
        self.assertTrue(C.road_visible('motorway', 8))

    def test_place_names_follow_the_same_visibility_in_both_layouts(self):
        self.assertFalse(C.place_visible('village', 10))
        self.assertTrue(C.place_visible('village', 12))
        self.assertTrue(C.place_visible('town', 8))
        self.assertFalse(C.place_visible('suburb', 10))


if __name__ == '__main__':
    unittest.main()
