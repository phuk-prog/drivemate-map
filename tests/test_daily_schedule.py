from datetime import date
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from daily_schedule import selected_cron


class DailyScheduleTests(unittest.TestCase):
    def test_winter_and_summer(self):
        self.assertEqual('0 1 * * *', selected_cron(date(2026, 1, 5)))
        self.assertEqual('0 0 * * *', selected_cron(date(2026, 7, 5)))

    def test_clock_changes_choose_once(self):
        self.assertEqual('0 1 * * *', selected_cron(date(2026, 3, 29)))
        self.assertEqual('0 0 * * *', selected_cron(date(2026, 10, 25)))
        self.assertEqual('0 1 * * *', selected_cron(date(2026, 10, 26)))


class TwiceDailyTests(unittest.TestCase):
    def test_01_and_14_uk_in_winter_and_summer(self):
        from daily_schedule import selected_crons
        self.assertEqual({'0 1 * * *', '0 14 * * *'}, selected_crons(date(2026, 1, 5)))
        self.assertEqual({'0 0 * * *', '0 13 * * *'}, selected_crons(date(2026, 7, 5)))
        # Clocks went back early on 25 Oct 2026: 01:00 is still BST, 14:00 is GMT.
        self.assertEqual({'0 0 * * *', '0 14 * * *'}, selected_crons(date(2026, 10, 25)))

    def test_latest_slot_is_fixed_for_a_whole_update_chain(self):
        from datetime import datetime, timezone
        from daily_schedule import latest_slot
        utc = timezone.utc
        self.assertEqual(datetime(2026, 10, 25, 0, tzinfo=utc), latest_slot(datetime(2026, 10, 25, 8, 45, tzinfo=utc)))
        self.assertEqual(datetime(2026, 10, 25, 14, tzinfo=utc), latest_slot(datetime(2026, 10, 25, 20, 0, tzinfo=utc)))
        self.assertEqual(datetime(2026, 10, 25, 14, tzinfo=utc), latest_slot(datetime(2026, 10, 26, 0, 59, tzinfo=utc)))
        self.assertEqual(datetime(2026, 7, 5, 13, tzinfo=utc), latest_slot(datetime(2026, 7, 5, 13, 0, tzinfo=utc)))


class SameWindowTests(unittest.TestCase):
    def test_runs_in_the_same_window_share_one_slot(self):
        # Category 1.7: a second scheduled run inside the same 01:00 / 14:00 window resolves to the same slot.
        from datetime import datetime, timezone
        from daily_schedule import latest_slot
        utc = timezone.utc
        first = latest_slot(datetime(2026, 10, 25, 14, 5, tzinfo=utc))
        second = latest_slot(datetime(2026, 10, 25, 15, 30, tzinfo=utc))
        self.assertEqual(first, second)
        self.assertEqual(datetime(2026, 10, 25, 14, tzinfo=utc), second)
        self.assertNotEqual(first, latest_slot(datetime(2026, 10, 26, 1, 5, tzinfo=utc)))
