import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import progressive_regions as regions
import test_publish_map_data as fixtures


def feature(key, parent, bounds, url=None):
    w, s, e, n = bounds
    return {'properties': {'id': key, 'parent': parent, 'name': key,
            'urls': {'pbf': url or 'https://download.geofabrik.de/europe/united-kingdom/england/' + key + '-latest.osm.pbf'}},
            'geometry': {'type': 'Polygon', 'coordinates': [[[w,s],[e,s],[e,n],[w,n],[w,s]]]}}


def catalog():
    return {'schema': 1, 'coverage': 'partial', 'regions': {}}


class ProgressiveRegionTests(unittest.TestCase):
    def test_stockport_first_then_manchester_then_small_extracts_never_whole_uk(self):
        index = {'features': [feature('united-kingdom', 'europe', [-8,49,2,61]),
            feature('england', 'united-kingdom', [-6,50,2,56]),
            feature('greater-manchester', 'england', [-2.8,53.2,-1.8,53.8]),
            feature('cheshire', 'england', [-3,53,-2,53.5]),
            feature('bermuda', 'united-kingdom', [-65,32,-64,33])]}
        plan = regions.expansion_plan(index)
        self.assertEqual(['stockport','manchester'], [r['id'] for r in plan[:2]])
        self.assertNotIn('england', [r['id'] for r in plan])
        self.assertNotIn('united-kingdom', [r['id'] for r in plan])
        self.assertNotIn('bermuda', [r['id'] for r in plan])
        self.assertIn('northern-ireland-area', [r['id'] for r in plan])

    def test_no_discovered_small_extracts_never_falls_back_to_uk(self):
        with self.assertRaises(ValueError):
            regions.expansion_plan({'features': []})

    def test_changed_active_region_updates_before_expanding(self):
        region = regions.INITIAL[0]
        value = catalog()
        tag = 'map-data-region-stockport-test'
        info = {'tag':tag, 'bytes':200, 'sha256':'a'*64}
        value['regions']['stockport'] = {**region,'content_sha256':'a'*64,'pipeline_sha256':'b'*64,
            'manifest':info,'map':{**info,'name':'drivemate-v1.pmtiles','layout':'drivemate-v1'},
            'observed_source_md5':'c'*32,'supplement_version':'v1'}
        signals = {regions.GM_SOURCE:'c'*32}
        self.assertEqual('manchester', regions.choose_next(regions.INITIAL,value,signals,'b'*64,'v1')['id'])
        signals[regions.GM_SOURCE] = 'd'*32
        self.assertEqual('stockport', regions.choose_next(regions.INITIAL,value,signals,'b'*64,'v1')['id'])
        signals[regions.GM_SOURCE] = 'c'*32
        self.assertEqual('stockport', regions.choose_next(regions.INITIAL,value,signals,'b'*64,'v2')['id'])

    def test_region_catalog_cannot_claim_complete_uk_or_other_region_assets(self):
        value = catalog(); value['coverage']='complete'
        with self.assertRaises(ValueError): regions.validate_catalog(value)
        for bounds in ([0,0,0,1], [0,0,float('nan'),1], [-200,0,2,1]):
            with self.assertRaises(ValueError): regions.checked_bounds(bounds)

    def test_real_github_adapter_can_bootstrap_catalog_release(self):
        github = regions.publisher.GitHub('owner/repo')
        calls = []
        def api(*args):
            calls.append(args)
            if args[0] == 'api':
                raise regions.publisher.GitHubError('HTTP 404')
            return ''
        github.run = api
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(catalog(), regions.load_catalog(github,directory))
        self.assertEqual(('api','repos/owner/repo/releases/tags/map-regions'),calls[0])
        for invalid in ('map-regions/../x', 'map-regions-unknown', '--map-regions'):
            with self.assertRaises(ValueError): github.release(invalid)

    def test_initial_missing_catalog_is_allowed_but_access_denied_is_not(self):
        class Missing:
            def __init__(self, code): self.code=code
            def inventory(self,tag): raise regions.publisher.GitHubError('HTTP '+str(self.code))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(catalog(),regions.load_catalog(Missing(404),directory))
            with self.assertRaises(regions.publisher.GitHubError): regions.load_catalog(Missing(403),directory)

    def test_publication_failure_does_not_advance_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=root/'out';out.mkdir()
            for name in ('drivemate-v1.pmtiles','regional-source.osm.pbf','source-inventory.json','NOTICE.md'):
                (out/name).write_text('synthetic-public-fixture')
            quality={'region':'stockport','errors':[],'assets':{p.name:{'bytes':p.stat().st_size,
                'sha256':regions.publisher.digest(p)} for p in out.iterdir()}}
            (out/'region-quality.json').write_text(json.dumps(quality))
            github=fixtures.FakeGitHub()
            github.fail_upload=('map-data-region-stockport-test','drivemate-v1.pmtiles')
            # Existing publisher module loaded separately by fixtures; use its error type.
            with patch.object(regions,'publish_catalog') as promote:
                with self.assertRaises(fixtures.publisher.GitHubError):
                    regions.publish_region(github,regions.INITIAL[0],out,'map-data-region-stockport-test',
                        {'content_sha256':'a'*64,'pipeline_sha256':'b'*64},catalog(),root)
                promote.assert_not_called()

    def test_changed_asset_after_validation_stops_before_any_remote_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=root/'out';out.mkdir()
            for name in ('drivemate-v1.pmtiles','regional-source.osm.pbf','source-inventory.json','NOTICE.md'):
                (out/name).write_text('synthetic-public-fixture')
            (out/'region-quality.json').write_text(json.dumps({'region':'stockport','errors':[],'assets':{}}))
            github=fixtures.FakeGitHub()
            with self.assertRaises(ValueError):
                regions.publish_region(github,regions.INITIAL[0],out,'map-data-region-stockport-test',{},catalog(),root)
            self.assertEqual([],github.actions)
