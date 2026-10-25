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


if __name__ == '__main__':
    unittest.main()
