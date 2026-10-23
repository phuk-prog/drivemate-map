import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
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


    def test_four_nations_are_prioritised_before_distant_english_extracts(self):
        index = {'features': [
            feature('united-kingdom', 'europe', [-9,49,2,61]),
            feature('england', 'united-kingdom', [-6,50,2,56]),
            feature('greater-manchester', 'england', [-2.8,53.2,-1.8,53.8]),
            feature('kent', 'england', [0.1,50.9,1.5,51.5]),
            feature('scotland', 'united-kingdom', [-8,54.5,-0.5,60.9]),
            feature('wales', 'united-kingdom', [-5.6,51.2,-2.6,53.5])]}
        plan = regions.expansion_plan(index)
        ids = [r['id'] for r in plan]
        self.assertEqual(['stockport','manchester','greater-manchester',
                          'wales','scotland','northern-ireland-area'], ids[:6])
        self.assertIn('kent', ids)

    def test_auto_expansion_checks_one_missing_source_not_every_previous_source(self):
        plan = regions.INITIAL + [
            {'id':'wales','source_url':'https://download.geofabrik.de/europe/united-kingdom/wales-latest.osm.pbf'}]
        published = catalog()
        published['regions'] = {'stockport':{}, 'manchester':{}}
        urls, next_region = regions.select_sources(plan,published,'workflow_dispatch')
        self.assertEqual('wales', next_region['id'])
        self.assertEqual({plan[2]['source_url']}, urls)
        daily_urls, daily_next = regions.select_sources(plan,published,'schedule')
        self.assertIsNone(daily_next)
        self.assertEqual({regions.GM_SOURCE,plan[2]['source_url']},daily_urls)

    def test_source_checksum_retries_transient_timeout_and_validates_identity(self):
        url=regions.GM_SOURCE
        calls=[]
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*args): return None
            def read(self,n): return (b'a'*32+b'  greater-manchester-latest.osm.pbf\\n')
        def opener(target,timeout):
            calls.append((target,timeout))
            if len(calls)<3: raise TimeoutError('temporary failure')
            return Reply()
        sleeps=[]
        signal=regions.read_source_signal(url,opener=opener,sleeper=sleeps.append)
        self.assertEqual('a'*32,signal)
        self.assertEqual([1,2],sleeps)
        self.assertEqual(3,len(calls))

    def test_source_checksum_failure_is_not_silently_treated_as_success(self):
        def timeout(*args,**kwargs): raise TimeoutError('offline')
        with self.assertRaisesRegex(RuntimeError,'after retries'):
            regions.read_source_signal(regions.GM_SOURCE,attempts=2,
                                       opener=timeout,sleeper=lambda _:None)
        class Invalid:
            def __enter__(self): return self
            def __exit__(self,*args): return None
            def read(self,n): return b'0'*32+b'  unexpected.osm.pbf\\n'
        with self.assertRaises(ValueError):
            regions.read_source_signal(regions.GM_SOURCE,opener=lambda *args,**kw:Invalid(),
                                       sleeper=lambda _:None)
        with self.assertRaises(ValueError):
            regions.read_source_signal('http://unsafe.example/file.osm.pbf')

    def test_permanent_http_checksum_error_is_not_retried(self):
        def denied(*args,**kwargs):
            raise HTTPError('https://download.geofabrik.de/test',403,'Forbidden',{},None)
        with self.assertRaises(HTTPError):
            regions.read_source_signal(regions.GM_SOURCE,opener=denied,
                                       sleeper=lambda _:self.fail('must not retry'))

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
