"""The UK overview map: what it keeps, how it is checked, and how it goes live."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import uk_overview as overview
import test_publish_map_data as fixtures


class PointerGitHub(fixtures.FakeGitHub):
    repo = 'phuk-prog/drivemate-map'

    def run(self, *args):
        assert args[:2] == ('release', 'upload') and '--clobber' in args
        path = Path(args[3])
        self.actions.append(('pointer', args[2], path.name))
        self.files.setdefault(args[2], {})[path.name] = path.read_bytes()


class OverviewTests(unittest.TestCase):
    def test_covers_the_uk_and_both_island_groups_at_low_zoom_only(self):
        self.assertEqual(10, overview.MAXZOOM)
        w, s, e, n = overview.BOUNDS
        for name, (lat, lon) in overview.SAMPLES.items():
            self.assertTrue(w < lon < e and s < lat < n, name)
        names = ' '.join(overview.SOURCES)
        for place in ('united-kingdom', 'isle-of-man', 'guernsey-jersey'):
            self.assertIn(place, names)

    def test_sea_is_drawn_well_beyond_the_uk(self):
        dw, ds, de, dn = overview.DRAWN
        w, s, e, n = overview.BOUNDS
        self.assertTrue(dw <= w - 10 and de >= e + 10 and ds <= s - 5 and dn >= n + 5)

    def test_keeps_main_roads_but_not_streets(self):
        roads = overview.FILTERS[0]
        for kind in ('motorway', 'trunk', 'primary', 'secondary'):
            self.assertIn(kind, roads)
        for kind in ('residential', 'service', 'tertiary', 'unclassified'):
            self.assertNotIn(kind, roads)

    def test_map_is_verified_before_the_pointer_moves(self):
        github = PointerGitHub()
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / overview.MAP_NAME
            archive.write_bytes(b'pmtiles' * 100)
            doc = overview.publish(github, archive, 'map-data-overview-r1-a1', tmp, overview.SOURCES)
        self.assertEqual('map-data-overview-r1-a1', doc['map']['tag'])
        kinds = [a[0] for a in github.actions]
        self.assertLess(max(i for i, k in enumerate(kinds) if k == 'upload'), kinds.index('pointer'))
        pointer = json.loads(github.files[overview.POINTER_TAG][overview.POINTER])
        self.assertEqual(overview.MAXZOOM, pointer['maxzoom'])
        self.assertEqual(700, pointer['map']['bytes'])

    def test_failed_map_upload_leaves_the_old_pointer(self):
        github = PointerGitHub()
        github.fail_upload = ('map-data-overview-r2-a1', overview.MAP_NAME)
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / overview.MAP_NAME
            archive.write_bytes(b'x' * 10)
            with self.assertRaises(Exception):
                overview.publish(github, archive, 'map-data-overview-r2-a1', tmp, overview.SOURCES)
        self.assertNotIn('pointer', [a[0] for a in github.actions])

    def test_pointer_rejects_another_kind_of_asset(self):
        good = {'schema': 1, 'maxzoom': 10, 'bounds': overview.BOUNDS,
                'map': {'tag': 'map-data-overview-r1-a1', 'bytes': 5, 'sha256': 'a' * 64,
                        'name': overview.MAP_NAME, 'layout': 'drivemate-v1'}}
        overview.validate_pointer(good)
        bad = json.loads(json.dumps(good)); bad['map']['tag'] = 'map-data-region-wales-r1-a1'
        with self.assertRaises(ValueError):
            overview.validate_pointer(bad)


if __name__ == '__main__':
    unittest.main()
