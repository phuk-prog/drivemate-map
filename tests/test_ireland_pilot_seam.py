"""Category 1 pilot seam: the Republic of Ireland / Northern Ireland border must have no gap.

The pilot pack is one Geofabrik extract (both sides of the border), so the border is
covered by the same source data and the pilot bounds must contain every crossing point
with margin. The pilot must also never reach the automatic UK queue.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import progressive_regions as regions  # noqa: E402

# (name, lon, lat, side): border crossings and towns either side of the Ireland / NI border.
BORDER_POINTS = [
    ('Dundalk', -6.40, 54.00, 'IE'),
    ('Newry', -6.34, 54.18, 'NI'),
    ('Pettigo', -7.78, 54.40, 'IE'),
    ('Belleek', -7.82, 54.50, 'NI'),
    ('Lifford', -7.47, 54.84, 'IE'),
    ('Derry', -7.31, 55.00, 'NI'),
]
MARGIN_DEGREES = 0.2


def inside(point, bounds, margin=0.0):
    lon, lat = point
    west, south, east, north = bounds
    return (west + margin <= lon <= east - margin) and (south + margin <= lat <= north - margin)


class IrelandPilotSeamTests(unittest.TestCase):
    def test_pilot_uses_one_source_extract_covering_both_sides_of_the_border(self):
        pilot = regions.pilot_region()
        self.assertEqual('ireland-pilot', pilot['id'])
        self.assertEqual('https://download.geofabrik.de/europe/ireland-and-northern-ireland-latest.osm.pbf',
                         pilot['source_url'])
        self.assertNotIn('greater-manchester', pilot['source_url'])
        self.assertTrue(pilot['inland'])

    def test_every_border_crossing_and_town_is_inside_the_pilot_bounds_with_margin(self):
        bounds = regions.pilot_region()['bounds']
        for name, lon, lat, side in BORDER_POINTS:
            with self.subTest(place=name, side=side):
                self.assertTrue(inside((lon, lat), bounds, MARGIN_DEGREES),
                                f'{name} ({side}) is outside the pilot bounds with margin')

    def test_pilot_sample_point_is_inside_its_bounds_and_is_dublin(self):
        pilot = regions.pilot_region()
        lon, lat = pilot['sample']
        self.assertTrue(inside((lon, lat), pilot['bounds'], MARGIN_DEGREES))
        self.assertAlmostEqual(53.3498, lat, places=3)

    def test_pilot_is_separate_from_the_automatic_uk_queue(self):
        self.assertNotIn('ireland-pilot', {r['id'] for r in regions.INITIAL})
        due = regions.pilot_due()
        self.assertEqual(['ireland-pilot'], [r['id'] for r in due])
        self.assertNotIn('ireland-pilot', regions.OUT_OF_SCOPE)

    def test_pilot_region_copy_cannot_change_the_module_definition(self):
        copy_ = regions.pilot_region()
        copy_['bounds'][0] = 0.0
        self.assertNotEqual(0.0, regions.pilot_region()['bounds'][0])

    def test_workflow_passes_pilot_flag_only_when_the_input_is_true(self):
        text = (ROOT / '.github' / 'workflows' / 'regional-maps.yml').read_text(encoding='utf-8')
        self.assertIn('if [ "$PILOT" = "true" ]; then pilot_flag="--pilot"; fi', text)
        self.assertIn('$pilot_flag', text)
        self.assertNotIn('--pilot"', text.replace('"--pilot"', '', 1))


if __name__ == '__main__':
    unittest.main()
