import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import uk_join


def lanes(*ids):
    return {'source': 'OSM', 'ways': [{'id': i, 't': {'lanes': '2'}, 'g': [[0, i], [1, i]]} for i in ids]}


class UkJoinTests(unittest.TestCase):
    def test_only_guidance_squares_and_uk_wide_files_are_joined(self):
        for name in ('lanes-106_-5.json', 'limits-106_-5.json', 'roadinfo-105_-6.json',
                     'road-details-106_-5.json.gz', 'places-211_-10.json.gz', 'cameras-uk.json', 'charge-zones-uk.json'):
            self.assertTrue(uk_join.joinable(name), name)
        for name in ('drivemate-v1.pmtiles', 'manifest.json', 'feature-packs-105_-5.jsonl.gz',
                     'regional-source.osm.pbf', 'NOTICE.md', 'lanes-106_-5.json.bak'):
            self.assertFalse(uk_join.joinable(name), name)

    def test_roads_in_both_regions_are_kept_once_newest_first(self):
        newer = lanes(1, 2)
        newer['ways'][0]['t']['lanes'] = '3'
        merged = uk_join.merge('lanes-106_-5.json', [newer, lanes(1, 3)])
        self.assertEqual([1, 2, 3], [w['id'] for w in merged['ways']])
        self.assertEqual('3', merged['ways'][0]['t']['lanes'], 'the newest region\'s copy wins')
        self.assertEqual('OSM', merged['source'])

    def test_speed_limits_without_ids_drop_identical_copies(self):
        a = {'ways': [[48.3, [[0, 0], [1, 1]]], [96.6, [[2, 2], [3, 3]]]]}
        b = {'ways': [[48.3, [[0, 0], [1, 1]]], [32.2, [[4, 4], [5, 5]]]]}
        self.assertEqual(3, len(uk_join.merge('limits-106_-5.json', [a, b])['ways']))

    def test_elements_zones_and_places_are_deduplicated(self):
        a = {'source': 'OSM', 'elements': [{'type': 'node', 'id': 1}, {'type': 'way', 'id': 1}]}
        b = {'source': 'OSM', 'elements': [{'type': 'node', 'id': 1}, {'type': 'node', 'id': 2}]}
        self.assertEqual(3, len(uk_join.merge('roadinfo-105_-6.json', [a, b])['elements']))
        z = {'source': 'OSM', 'zones': [{'name': 'ULEZ'}]}
        self.assertEqual(1, len(uk_join.merge('charge-zones-uk.json', [z, z])['zones']))
        rows = [['Shop', 'shop', 'Town', 53.0, -2.0]]
        self.assertEqual(rows, uk_join.merge('places-211_-10.json.gz', [rows, rows + []]))

    def test_unchanged_priority_reuses_and_reversed_priority_invalidates(self):
        copies = [{'sha256': 'b' * 64, 'bytes': 1, 'tag': 'map-data-region-x-r1-a1'},
                  {'sha256': 'a' * 64, 'bytes': 1, 'tag': 'map-data-region-y-r1-a1'}]
        self.assertNotEqual(uk_join.sources_key(copies), uk_join.sources_key(list(reversed(copies))))
        previous = {'lanes-1_1.json': {'sha256': 'c' * 64, 'bytes': 9, 'tag': 'map-data-ukjoin-r5-a1',
                                       'sources': uk_join.sources_key(copies)}}
        built, reused = uk_join.build(None, {'lanes-1_1.json': copies}, previous, tempfile.mkdtemp())
        self.assertEqual({}, built)
        self.assertEqual(previous, reused)

    def test_identity_follows_each_regions_manifest(self):
        catalog = {'regions': {'b': {'manifest': {'sha256': '2'}}, 'a': {'manifest': {'sha256': '1'}}}}
        self.assertEqual({'a': '1', 'b': '2'}, uk_join.identity(catalog))

    def test_plan_lists_copies_in_region_order(self):
        manifests = [('newer', {'lanes-1_1.json': {'sha256': 'x'}, 'drivemate-v1.pmtiles': {'sha256': 'm'}}),
                     ('older', {'lanes-1_1.json': {'sha256': 'y'}})]
        self.assertEqual({'lanes-1_1.json': [{'sha256': 'x', 'region': 'newer'}, {'sha256': 'y', 'region': 'older'}]},
                         uk_join.plan(manifests))

    def test_gzip_output_is_repeatable(self):
        folder = Path(tempfile.mkdtemp())
        a, b = folder / 'a' / 'road-details-1_1.json.gz', folder / 'b' / 'road-details-1_1.json.gz'
        for p in (a, b):
            p.parent.mkdir()
            uk_join.write(p, lanes(1))
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(lanes(1), json.loads(gzip.decompress(a.read_bytes())))

    def test_run_tags_are_strict(self):
        self.assertTrue(uk_join.RUN_TAG.fullmatch('map-data-ukjoin-r123-a1'))
        self.assertTrue(uk_join.RUN_TAG.fullmatch('map-data-ukjoin-r123-a1-part002'))
        self.assertFalse(uk_join.RUN_TAG.fullmatch('map-data-region-x-r1-a1'))
        self.assertFalse(uk_join.RUN_TAG.fullmatch('map-data-ukjoin'))

    def test_coverage_counts_roads_with_each_driving_tag(self):
        doc = {'ways': [{'id': 1, 't': {'maxheight': '4.2', 'bridge': 'yes'}}, {'id': 2, 't': {'surface': 'asphalt'}}]}
        counts = uk_join.tag_counts(doc)
        self.assertEqual(2, counts['roads'])
        self.assertEqual(1, counts['maxheight'])
        self.assertEqual(0, counts['ford'])
        files = {'road-details-1_1.json.gz': {'tags': counts}, 'road-details-1_2.json.gz': {'tags': counts},
                 'lanes-1_1.json': {'tags': {'roads': 99}}}
        total = uk_join.coverage(files)
        self.assertEqual(4, total['roads'])
        self.assertEqual(2, total['maxheight'])


if __name__ == '__main__':
    unittest.main()
