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


class UkSearchJoinTests(unittest.TestCase):
    """C08: one UK offline-search list, so a postcode outside the phone's region is found."""

    H = '#drivemate-search-offline\t1\t{}\t{}'

    def write_region(self, folder, date, credit, rows):
        path = Path(folder) / uk_join.SEARCH
        with gzip.open(path, 'wt', encoding='utf-8', newline='\n') as f:
            f.write(self.H.format(date, credit) + '\n')
            for row in sorted(rows):
                f.write(row + '\n')
        return path

    def test_search_list_is_joined_uk_wide(self):
        self.assertTrue(uk_join.joinable(uk_join.SEARCH))

    def test_regions_combine_into_one_sorted_deduplicated_list(self):
        stockport = ['sk43ab\tP\t\tStockport\t53.4100\t-2.1600']
        staffs = ['st48jg\tP\t\tStoke-on-Trent\t52.9645\t-2.1978', 'trentham\tL\tTrentham\tStoke-on-Trent\t52.9670\t-2.2000']
        cheshire = ['st48jg\tP\t\tStoke-on-Trent\t52.9645\t-2.1978', 'sk43ab\tP\t\tStockport\t53.4100\t-2.1600']
        with tempfile.TemporaryDirectory() as tmp:
            docs = []
            for i, (date, credit, rows) in enumerate([
                    ('2026-10-30', 'Contains OS data', stockport),
                    ('2026-10-31', 'Contains OS data; Contains OSNI data', staffs),
                    ('2026-10-29', 'Contains OS data', cheshire)]):
                (Path(tmp) / str(i)).mkdir()
                docs.append(uk_join.read(self.write_region(Path(tmp) / str(i), date, credit, rows)))
            header, rows = uk_join.merge(uk_join.SEARCH, docs)
            self.assertEqual('#drivemate-search-offline\t1\t2026-10-31\tContains OS data; Contains OSNI data', header)
            self.assertEqual(sorted(set(stockport + staffs + cheshire)), rows)
            self.assertEqual(1, sum(r.startswith('st48jg\t') for r in rows), 'overlapping regions keep one copy')
            out = Path(tmp) / 'out' / uk_join.SEARCH
            out.parent.mkdir()
            uk_join.write(out, (header, rows))
            first = out.read_bytes()
            self.assertEqual((header, rows), uk_join.read(out), 'the joined list loads as the app reads it')
            uk_join.write(out, (header, rows))
            self.assertEqual(first, out.read_bytes(), 'same content gives the same bytes')

    def test_unsorted_or_malformed_lists_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / uk_join.SEARCH
            with gzip.open(path, 'wt', encoding='utf-8') as f:
                f.write(self.H.format('2026-10-31', 'Contains OS data') + '\nzz\tP\t\tA\t1\t1\naa\tP\t\tB\t1\t1\n')
            with self.assertRaisesRegex(ValueError, 'not sorted'):
                uk_join.read(path)
            with gzip.open(path, 'wt', encoding='utf-8') as f:
                f.write('not a header\naa\tP\t\tB\t1\t1\n')
            with self.assertRaisesRegex(ValueError, 'Invalid'):
                uk_join.read(path)
            with self.assertRaisesRegex(ValueError, 'header'):
                uk_join.merge(uk_join.SEARCH, [('#other\t1\t2026-10-31\tx', [])])


class UkSearchPostcodePointTests(unittest.TestCase):
    def test_named_centroid_wins_over_address_points(self):
        lines = {'st48jg\tP\t\tStoke-on-Trent\t52.9645\t-2.1978', 'st48jg\tP\t\t\t52.96500\t-2.19800',
                 'st48jg\tP\t\t\t52.96400\t-2.19700', 'trentham\tS\tTrentham\tStoke\t52.9659\t-2.1869'}
        out = sorted(uk_join.one_point_per_postcode(lines))
        self.assertEqual(['st48jg\tP\t\tStoke-on-Trent\t52.9645\t-2.1978', 'trentham\tS\tTrentham\tStoke\t52.9659\t-2.1869'], out)

    def test_address_points_only_collapse_to_their_mean(self):
        lines = {'ll758rj\tP\t\t\t53.30000\t-4.21000', 'll758rj\tP\t\t\t53.30200\t-4.21200'}
        self.assertEqual(['ll758rj\tP\t\t\t53.30100\t-4.21100'], uk_join.one_point_per_postcode(lines))

    def test_merge_keeps_one_line_per_postcode_and_stays_sorted(self):
        h = '#drivemate-search-offline\t1\t2026-10-31\tContains OS data'
        a = (h, ['sk43ab\tP\t\tStockport\t53.4158\t-2.2123', 'sk43ab\tP\t\t\t53.41600\t-2.21200'])
        b = (h, ['sk43ab\tP\t\t\t53.41500\t-2.21300', 'stockport\tT\tStockport\tGreater Manchester\t53.4083\t-2.1494'])
        _, rows = uk_join.merge(uk_join.SEARCH, [a, b])
        self.assertEqual(1, sum(r.startswith('sk43ab\t') for r in rows))
        self.assertEqual(sorted(rows), rows)


class UkSearchAddressPointTests(unittest.TestCase):
    def test_same_address_nearby_collapses_but_far_apart_is_kept(self):
        near = ['1 high street\tO\t1 High Street\tTown\t53.00000\t-2.00000',
                '1 high street\tO\t1 High Street\tTown\t53.00100\t-2.00000']  # ~111 m apart
        far = ['1 high street\tO\t1 High Street\tTown\t53.10000\t-2.00000']    # ~11 km away
        out = sorted(uk_join.one_point_per_postcode(set(near + far)))
        self.assertEqual(['1 high street\tO\t1 High Street\tTown\t53.00050\t-2.00000',
                          '1 high street\tO\t1 High Street\tTown\t53.10000\t-2.00000'], out)

    def test_different_names_or_areas_never_merge(self):
        lines = {'1 high street\tO\t1 High Street\tTown\t53.0\t-2.0', '1 high street\tO\t1 High Street\tOther\t53.0\t-2.0',
                 'high street\tR\tHigh Street\tTown\t53.0\t-2.0', 'high street\tR\tHigh Street\tTown\t53.0\t-2.0001'}
        self.assertEqual(sorted(lines), sorted(uk_join.one_point_per_postcode(lines)))
