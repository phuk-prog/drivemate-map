"""The two UK scheduled map updates are the only automatic large publishers."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]

def events(name):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    return workflow.get("on", workflow.get(True)), workflow

class WorkflowSchedulingContract(unittest.TestCase):
    def test_twice_daily_timezone_guard_and_no_expensive_push_builds(self):
        crons = {'0 0 * * *', '0 1 * * *', '0 13 * * *', '0 14 * * *'}
        for name in ('regional-maps.yml', 'uk-overview.yml'):
            with self.subTest(name=name):
                event, doc = events(name)
                self.assertIn('workflow_dispatch', event)
                self.assertEqual(crons, {x['cron'] for x in event['schedule']})
                self.assertNotIn('push', event)
                self.assertFalse(doc['concurrency']['cancel-in-progress'])

    def test_legacy_builder_no_longer_autopublishes(self):
        event, doc = events('map-data.yml')
        self.assertIn('workflow_dispatch', event)
        self.assertNotIn('schedule', event)
        self.assertNotIn('push', event)
        self.assertTrue(event['workflow_dispatch']['inputs']['validate_only']['default'])
        self.assertFalse(doc['concurrency']['cancel-in-progress'])

    def test_hourly_watchdog_and_pr_checks_remain(self):
        event, _ = events('map-watchdog.yml')
        self.assertEqual({'40 * * * *'}, {x['cron'] for x in event['schedule']})
        source, _ = events('source-checks.yml')
        self.assertIn('pull_request', source)
        self.assertIn('push', source)
