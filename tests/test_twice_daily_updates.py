"""Twice-daily updates: islands, failed regions held back, shared OSM hourly changes."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import osm_changes
import progressive_regions as regions
from test_progressive_regions import feature, catalog


def published(region, slot=None, md5='c' * 32):
    info = {'tag': 'map-data-region-' + region['id'] + '-t', 'bytes': 200, 'sha256': 'a' * 64}
    entry = {**region, 'content_sha256': 'a' * 64, 'pipeline_sha256': 'b' * 64, 'manifest': info,
             'map': {**info, 'name': 'drivemate-v1.pmtiles', 'layout': 'drivemate-v1'},
             'observed_source_md5': md5, 'supplement_version': 'v1'}
    if slot:
        entry['osm_changes_slot'] = slot
    return entry


class IslandTests(unittest.TestCase):
    def test_isle_of_man_and_channel_islands_are_planned(self):
        index = {'features': [
            feature('united-kingdom', 'europe', [-9, 49, 2, 61]),
            feature('england', 'united-kingdom', [-6, 50, 2, 56]),
            feature('greater-manchester', 'england', [-2.8, 53.2, -1.8, 53.8]),
            feature('isle-of-man', 'europe', [-4.9, 54.0, -4.3, 54.5],
                    'https://download.geofabrik.de/europe/isle-of-man-latest.osm.pbf'),
            feature('guernsey-jersey', 'europe', [-2.7, 49.1, -2.0, 49.8],
                    'https://download.geofabrik.de/europe/guernsey-jersey-latest.osm.pbf'),
            feature('france', 'europe', [-5, 41, 10, 51],
                    'https://download.geofabrik.de/europe/france-latest.osm.pbf')]}
        ids = [r['id'] for r in regions.expansion_plan(index)]
        self.assertIn('isle-of-man', ids)
        self.assertIn('guernsey-jersey', ids)
        self.assertNotIn('france', ids)


class HeldBackTests(unittest.TestCase):
    def test_failed_region_is_skipped_and_the_next_one_is_chosen(self):
        value = catalog()
        for r in regions.INITIAL:
            value['regions'][r['id']] = published(r)
        signals = {regions.GM_SOURCE: 'd' * 32}  # both changed
        self.assertEqual('stockport', regions.choose_next(regions.INITIAL, value, signals, 'b' * 64, 'v1')['id'])
        self.assertEqual('manchester', regions.choose_next(regions.INITIAL, value, signals, 'b' * 64, 'v1',
                                                            skip={'stockport'})['id'])
        self.assertIsNone(regions.choose_next(regions.INITIAL, value, signals, 'b' * 64, 'v1',
                                              skip={'stockport', 'manchester'}))

    def test_unreachable_change_signal_only_holds_back_its_own_regions(self):
        def reader(url):
            if 'wales' in url:
                raise RuntimeError('timed out')
            return 'c' * 32
        urls = {regions.GM_SOURCE, 'https://download.geofabrik.de/europe/united-kingdom/wales-latest.osm.pbf'}
        self.assertEqual({regions.GM_SOURCE: 'c' * 32}, regions.read_signals(urls, reader))

    def test_new_update_slot_rebuilds_each_region_once(self):
        value = catalog()
        for r in regions.INITIAL:
            value['regions'][r['id']] = published(r, slot='2026-10-25T00:00Z')
        signals = {regions.GM_SOURCE: 'c' * 32}
        self.assertIsNone(regions.choose_next(regions.INITIAL, value, signals, 'b' * 64, 'v1', '2026-10-25T00:00Z'))
        self.assertEqual('stockport', regions.choose_next(regions.INITIAL, value, signals, 'b' * 64, 'v1',
                                                           '2026-10-25T14:00Z')['id'])
        value['regions']['stockport']['osm_changes_slot'] = '2026-10-25T14:00Z'
        self.assertEqual('manchester', regions.choose_next(regions.INITIAL, value, signals, 'b' * 64, 'v1',
                                                            '2026-10-25T14:00Z')['id'])


class FakeServer:
    def timestamp_to_sequence(self, when):
        return 1000 + int(when.timestamp() // 3600) - int(datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp() // 3600)


class FakeGitHub:
    def __init__(self):
        self.assets = {}
        self.uploads = 0

    def ensure_release(self, tag):
        assert tag == 'map-data-sources'

    def inventory(self, tag):
        return dict(self.assets)

    def upload_many(self, tag, paths):
        self.uploads += 1
        for p in paths:
            self.assets[Path(p).name] = Path(p).read_bytes()

    def download(self, tag, name, directory):
        target = Path(directory) / name
        target.write_bytes(self.assets[name])
        return target


class ChangeFileTests(unittest.TestCase):
    slot = datetime(2026, 10, 25, 14, tzinfo=timezone.utc)

    def test_diff_paths_follow_the_replication_layout(self):
        self.assertEqual('006/123/045.osc.gz', osm_changes.diff_path(6123045))

    def test_window_of_hourly_diffs_is_merged_into_one_file(self):
        fetched, commands = [], []
        def fetcher(url, target):
            fetched.append(url)
            Path(target).write_bytes(b'x')
        def run(cmd, check):
            commands.append(cmd)
            Path(cmd[cmd.index('-o') + 1]).write_bytes(b'merged')
        with tempfile.TemporaryDirectory() as tmp:
            merged, info = osm_changes.build(self.slot, tmp, server=FakeServer(), fetcher=fetcher, run=run)
            self.assertEqual('osm-changes-20261025T14Z.osc.gz', merged.name)
            self.assertEqual(30, len(fetched))
            self.assertTrue(fetched[0].startswith('https://planet.osm.org/replication/hour/'))
            self.assertEqual(['osmium', 'merge-changes', '--simplify'], commands[0][:3])
            self.assertEqual([merged.name], [p.name for p in Path(tmp).iterdir()])

    def test_change_file_is_built_once_per_slot_and_reused(self):
        github, builds = FakeGitHub(), []
        def builder(slot, directory):
            builds.append(slot)
            p = Path(directory) / osm_changes.asset_name(slot)
            p.write_bytes(b'changes')
            return p, {}
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            osm_changes.ensure(github, self.slot, a, builder=builder)
            again = osm_changes.ensure(github, self.slot, b, builder=builder)
            self.assertEqual(b'changes', again.read_bytes())
        self.assertEqual(1, len(builds))
        self.assertEqual(1, github.uploads)

    def test_permanent_http_error_is_not_retried(self):
        from urllib.error import HTTPError
        calls = []
        def opener(url, timeout):
            calls.append(url)
            raise HTTPError(url, 404, 'missing', None, None)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(HTTPError):
            osm_changes.fetch('https://planet.osm.org/x', Path(tmp) / 'x', opener=opener, sleeper=lambda s: None)
        self.assertEqual(1, len(calls))

    def test_timeouts_are_retried_then_reported(self):
        calls = []
        def opener(url, timeout):
            calls.append(url)
            raise TimeoutError('slow')
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(RuntimeError):
            osm_changes.fetch('https://planet.osm.org/x', Path(tmp) / 'x', opener=opener, sleeper=lambda s: None)
        self.assertEqual(6, len(calls))


if __name__ == '__main__':
    unittest.main()


class MetadataRetryTests(unittest.TestCase):
    def test_geofabrik_index_timeout_is_retried(self):
        import io
        calls = []
        def opener(url, timeout):
            calls.append(url)
            if len(calls) < 3:
                raise TimeoutError('slow')
            return io.BytesIO(b'{"features": []}')
        self.assertEqual({'features': []}, regions.read_json_url('https://download.geofabrik.de/index-v1.json',
                                                                 opener=opener, sleeper=lambda s: None))
        self.assertEqual(3, len(calls))


class ParallelUpdateTests(unittest.TestCase):
    def test_plan_lists_every_region_behind_the_slot_once(self):
        value = catalog()
        value['regions']['stockport'] = published(regions.INITIAL[0], slot='2026-10-25T00:00Z')
        signals = {regions.GM_SOURCE: 'c' * 32}
        due = regions.due_regions(regions.INITIAL, value, signals, 'b' * 64, 'v1', '2026-10-25T14:00Z')
        self.assertEqual(['stockport', 'manchester'], [r['id'] for r in due])
        self.assertEqual(['manchester'], [r['id'] for r in regions.due_regions(
            regions.INITIAL, value, signals, 'b' * 64, 'v1', '2026-10-25T00:00Z')])

    def test_promote_adds_every_built_region_in_one_change(self):
        live = catalog()
        live['regions']['stockport'] = published(regions.INITIAL[0])
        merged = regions.merge_entries(live, [published(regions.INITIAL[0], slot='S'), published(regions.INITIAL[1], slot='S')])
        self.assertEqual({'stockport', 'manchester'}, set(merged['regions']))
        self.assertEqual('S', merged['regions']['stockport']['osm_changes_slot'])
        self.assertNotIn('osm_changes_slot', live['regions']['stockport'])

    def test_promote_refuses_a_malformed_entry(self):
        with self.assertRaises(ValueError):
            regions.merge_entries(catalog(), [{'id': '../catalog'}])


class FingerprintTests(unittest.TestCase):
    def test_overview_and_watchdog_changes_do_not_rebuild_regions(self):
        import shutil, daily_source_probe
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            for name in ('build.py', 'uk_overview.py'):
                (root / 'scripts' / name).write_text('x = 1\n')
            (root / '.github/workflows').mkdir(parents=True)
            (root / '.github/workflows/map-data.yml').write_text('a\n')
            fp = lambda: daily_source_probe.pipeline_fingerprint(root, excludes=regions.NOT_BUILD_INPUTS)['pipeline_sha256']
            before = fp()
            (root / 'scripts/uk_overview.py').write_text('x = 2\n')
            self.assertEqual(before, fp())
            (root / 'scripts/build.py').write_text('x = 2\n')
            self.assertNotEqual(before, fp())
