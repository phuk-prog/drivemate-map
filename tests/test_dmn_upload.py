import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('dmn_upload', Path(__file__).resolve().parents[1] / 'scripts/dmn_upload.py')
dmn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dmn)
RateLimited = dmn.publisher.RateLimited
TAG = 'dmn-uk-20261031-32'


class FakeGitHub:
    """Release store whose uploads can be cut off by a rate limit after `allow` assets."""

    def __init__(self, allow=None, create_limited=0):
        self.assets, self.created, self.waits, self.deleted = {}, False, 0, []
        self.allow, self.create_limited, self.next_id = allow, create_limited, 1

    def ensure_release(self, tag):
        if self.create_limited:
            self.create_limited -= 1
            raise RateLimited('HTTP 403: API rate limit exceeded for installation')
        self.created = True

    def inventory(self, tag):
        assert self.created
        return {n: dict(a) for n, a in self.assets.items()}

    def delete(self, asset):
        self.deleted.append(asset['name'])
        del self.assets[asset['name']]

    def upload_many(self, tag, paths):
        for path in paths:
            if path.name in self.assets:
                raise AssertionError('uploaded an existing asset again')
            if self.allow is not None:
                if self.allow == 0:
                    # Cut off mid-asset: GitHub keeps a half-finished "starter" asset.
                    self.assets[path.name] = {'id': self.next_id, 'name': path.name, 'state': 'starter', 'size': 0}
                    self.next_id += 1
                    raise RateLimited('HTTP 403: API rate limit exceeded for installation')
                self.allow -= 1
            self.assets[path.name] = {'id': self.next_id, 'name': path.name, 'state': 'uploaded',
                                      'size': path.stat().st_size}
            self.next_id += 1

    def wait_for_rate_limit(self):
        self.waits += 1
        self.allow = None  # allowance reset


class DmnUploadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = []
        for i in range(95):
            path = Path(self.temp.name) / f'dmn-{i:03d}.dmnpack'
            path.write_bytes(b'DMNPACK2' + bytes([i]) * (100 + i))
            self.paths.append(path)

    def test_rate_limit_part_way_resumes_and_reuploads_half_finished_asset(self):
        github = FakeGitHub(allow=50)
        self.assertEqual(dmn.upload(github, TAG, self.paths, batch=40), 95)
        self.assertEqual(github.waits, 1)
        self.assertEqual(github.deleted, ['dmn-050.dmnpack'])
        self.assertTrue(all(a['state'] == 'uploaded' for a in github.assets.values()))
        self.assertEqual(len(github.assets), 95)

    def test_rate_limited_release_create_is_retried(self):
        github = FakeGitHub(create_limited=1)
        self.assertEqual(dmn.upload(github, TAG, self.paths), 95)
        self.assertEqual(github.waits, 1)

    def test_partial_release_from_an_earlier_failed_run_is_completed_not_replaced(self):
        github = FakeGitHub(allow=30)
        with self.assertRaises(RateLimited):
            dmn.upload(github, TAG, self.paths, waits=0)
        before = {n: a['id'] for n, a in github.assets.items() if a['state'] == 'uploaded'}
        github.allow = None
        dmn.upload(github, TAG, self.paths)
        for name, asset_id in before.items():
            self.assertEqual(github.assets[name]['id'], asset_id)
        self.assertEqual(len(github.assets), 95)

    def test_gives_up_after_bounded_waits(self):
        class AlwaysLimited(FakeGitHub):
            def wait_for_rate_limit(self):
                self.waits += 1
                self.allow = 0
        github = AlwaysLimited(allow=0)
        with self.assertRaises(RateLimited):
            dmn.upload(github, TAG, self.paths, waits=2)
        self.assertEqual(github.waits, 2)

    def test_unexpected_or_different_assets_are_refused(self):
        github = FakeGitHub()
        github.created = True
        github.assets['foreign.bin'] = {'id': 9, 'name': 'foreign.bin', 'state': 'uploaded', 'size': 1}
        with self.assertRaisesRegex(ValueError, 'unexpected asset'):
            dmn.upload(github, TAG, self.paths)
        github.assets = {'dmn-000.dmnpack': {'id': 9, 'name': 'dmn-000.dmnpack', 'state': 'uploaded', 'size': 1}}
        with self.assertRaisesRegex(ValueError, 'differs'):
            dmn.upload(github, TAG, self.paths)

    def test_only_dated_dmn_releases_and_piece_names(self):
        for tag in ('dmn-uk', 'map-data-uk', 'dmn-uk-2026-1', ''):
            with self.assertRaises(ValueError):
                dmn.upload(FakeGitHub(), tag, self.paths)
        bad = Path(self.temp.name) / 'dmn-uk.json'
        bad.write_text('{}')
        with self.assertRaises(ValueError):
            dmn.upload(FakeGitHub(), TAG, [bad])

    def test_client_never_touches_live_pointer_release(self):
        client = dmn.DmnGitHub('phuk-prog/drivemate-map')
        with self.assertRaises(ValueError):
            client.release('dmn-uk')


if __name__ == '__main__':
    unittest.main()
