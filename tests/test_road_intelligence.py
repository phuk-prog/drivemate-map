from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import road_intelligence as road


class RoadIntelligenceTests(unittest.TestCase):
    now = datetime(2026, 10, 23, 1, tzinfo=timezone.utc)

    def test_missing_key_is_not_a_checked_empty_feed(self):
        def forbidden(*args):
            self.fail('No network without configured key')
        value = road.collect(self.now, '', fetch=forbidden)
        self.assertFalse(value['complete'])
        self.assertFalse(value['routing_authoritative'])
        self.assertEqual({'setup_required'}, {i['status'] for i in value['sources']})

    def test_pagination_keeps_all_pages_and_sends_key_only_to_official_api(self):
        calls = []
        def fetch(url, headers):
            calls.append((url, headers))
            return {'D2Payload': {'situation': [], 'publicationTime': self.now.isoformat()}}, (
                '?pageCursor=2' if len(calls) == 1 else '')
        pages = road.nh_pages(road.NH + '/roads/v2.0/closures', 'test-placeholder', fetch, lambda _: None)
        self.assertEqual(2, len(pages))
        self.assertEqual('test-placeholder', calls[1][1]['Ocp-Apim-Subscription-Key'])

    def test_cross_host_and_cross_path_pagination_is_rejected_before_request(self):
        for next_url in ('https://example.com/leak', '/unapproved', 'http://api.data.nationalhighways.co.uk/roads/v2.0/closures'):
            calls = []
            def fetch(url, headers):
                calls.append(url)
                return {'D2Payload': {'situation': []}}, next_url
            with self.assertRaises(ValueError):
                road.nh_pages(road.NH + '/roads/v2.0/closures', 'test-placeholder', fetch, lambda _: None)
            self.assertEqual(1, len(calls))

    def test_failed_or_malformed_response_never_looks_like_no_closures(self):
        for page in ({'error': 'denied'}, {'D2Payload': {}}):
            value = road.collect(self.now, 'test-placeholder', lambda *_: (page, ''), lambda _: None)
            self.assertFalse(value['complete'])
            self.assertEqual({'unavailable'}, {i['status'] for i in value['sources']})

    def test_snapshot_expires_and_is_not_authoritative_for_routing(self):
        value = road.collect(self.now, 'test-placeholder', lambda *_: ({'D2Payload': {'situation': []}}, ''), lambda _: None)
        self.assertTrue(value['complete'])
        self.assertEqual('2026-10-23T01:10:00+00:00', value['expires_utc'])
        self.assertFalse(value['routing_authoritative'])

    def test_repeated_pagination_cannot_report_complete(self):
        url = road.NH + '/roads/v2.0/closures'
        with self.assertRaises(ValueError):
            road.nh_pages(url, 'test-placeholder', lambda *_: ({'D2Payload': {'situation': []}}, url), lambda _: None)
