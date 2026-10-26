from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import map_watchdog as w

NOW = datetime(2026, 10, 25, 16, 40, tzinfo=timezone.utc)  # latest slot 14:00 UTC (GMT)
SLOT = '2026-10-25T14:00Z'
FRESH = {'built_utc': '2026-10-25T14:09:00+00:00'}


def catalog(**slots):
    return {'regions': {k: {'osm_changes_slot': v} for k, v in slots.items()}}


class WatchdogTests(unittest.TestCase):
    def test_stalled_update_is_restarted(self):
        start, notes = w.decide(False, False, catalog(wales=SLOT, scotland='2026-10-25T00:00Z'), FRESH, NOW)
        self.assertEqual([w.REGIONS_WORKFLOW], start)
        self.assertIn('scotland', notes[0])

    def test_running_update_is_left_alone(self):
        start, _ = w.decide(True, False, catalog(scotland='2026-10-25T00:00Z'), FRESH, NOW)
        self.assertEqual([], start)

    def test_finished_update_starts_nothing(self):
        start, _ = w.decide(False, False, catalog(wales=SLOT, scotland=SLOT), FRESH, NOW)
        self.assertEqual([], start)

    def test_old_overview_is_rebuilt(self):
        start, _ = w.decide(False, False, catalog(wales=SLOT), {'built_utc': '2026-10-25T09:02:08+00:00'}, NOW)
        self.assertEqual([w.OVERVIEW_WORKFLOW], start)
        start, _ = w.decide(False, True, catalog(wales=SLOT), None, NOW)
        self.assertEqual([], start)

    def test_uk_join_follows_the_finished_regions(self):
        done = {'regions': {'wales': {'osm_changes_slot': SLOT, 'manifest': {'sha256': 'new'}}}}
        start, notes = w.decide(False, False, done, FRESH, NOW, join_pointer={'regions': {'wales': 'old'}}, check_join=True)
        self.assertEqual([w.JOIN_WORKFLOW], start)
        start, _ = w.decide(False, False, done, FRESH, NOW, join_pointer=None, check_join=True)
        self.assertEqual([w.JOIN_WORKFLOW], start, 'no join published yet')
        start, notes = w.decide(False, False, done, FRESH, NOW, join_pointer={'regions': {'wales': 'new'}}, check_join=True)
        self.assertEqual([], start)
        self.assertIn('UK join up to date', notes)

    def test_uk_join_waits_while_regions_build_or_it_runs(self):
        behind = {'regions': {'wales': {'osm_changes_slot': '2026-10-25T00:00Z', 'manifest': {'sha256': 'new'}}}}
        start, _ = w.decide(True, False, behind, FRESH, NOW, join_pointer=None, check_join=True)
        self.assertEqual([], start)
        done = {'regions': {'wales': {'osm_changes_slot': SLOT, 'manifest': {'sha256': 'new'}}}}
        start, _ = w.decide(False, False, done, FRESH, NOW, active_join=True, join_pointer=None, check_join=True)
        self.assertEqual([], start)

    def test_uk_map_is_remade_after_changed_regions(self):
        done = {'regions': {'wales': {'osm_changes_slot': SLOT, 'manifest': {'sha256': 'new'}}}}
        start, _ = w.decide(False, False, done, FRESH, NOW, map_pointer={'regions': {'wales': 'old'}}, check_map=True)
        self.assertEqual([w.MAP_WORKFLOW], start)
        start, _ = w.decide(False, False, done, FRESH, NOW, active_map=True, map_pointer=None, check_map=True)
        self.assertEqual([], start)
        start, notes = w.decide(False, False, done, FRESH, NOW, map_pointer={'regions': {'wales': 'new'}}, check_map=True)
        self.assertEqual([], start)
        self.assertIn('UK map up to date', notes)


if __name__ == '__main__':
    unittest.main()
