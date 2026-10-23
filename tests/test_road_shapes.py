"""The shape checker must detect a bend flattened to a chord and refuse missing roads."""
import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_road_shapes as C


class RoadShapes(unittest.TestCase):
    def test_numbered_motorway_bend_does_not_need_a_name_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            feature = {'properties': {'highway': 'motorway', 'ref': 'M6'},
                       'geometry': {'type': 'LineString', 'coordinates': [[0, 0], [.001, .001], [.002, 0]]}}
            (path / 'M6.geojsonseq').write_text(json.dumps(feature) + '\n')
            self.assertEqual(len(C.references(path, {'id': 'M6', 'lat': 0, 'lon': 0})['motorway']), 3)

    def test_flatter_bend_has_more_error_than_preserved_bend(self):
        bend = [(0, 0), (1, 1), (2, 0)]
        chord = [((0, 0), (2, 0))]
        kept = list(zip(bend, bend[1:]))
        self.assertEqual(C.distances(bend, kept), [0, 0, 0])
        self.assertEqual(C.distances(bend, chord), [0, 1, 0])

    def test_distance_is_to_segment_not_its_infinite_line(self):
        self.assertEqual(C.distances([(3, 0)], [((0, 0), (1, 0))]), [2])
        self.assertEqual(C.distances([(0, 2)], [((0, 0), (0, 0))]), [2])

    def test_missing_candidate_road_is_not_zero_error(self):
        with self.assertRaises(ValueError):
            C.distances([(0, 0)], [])

    def test_world_projection_and_pixel_scale(self):
        self.assertEqual(C.world(0, 0), (0.5, 0.5))
        self.assertAlmostEqual(C.distances([(0, 1 / 512)], [((0, 0), (1, 0))])[0] * 512, 1)

    def test_comparison_refuses_regression_and_empty_results(self):
        self.assertTrue(C.no_worse([1, 2, 3], [0, 1, 2]))
        self.assertFalse(C.no_worse([0, 0, 0], [0, 0, 1]))
        self.assertFalse(C.no_worse([], []))


if __name__ == '__main__':
    unittest.main()
