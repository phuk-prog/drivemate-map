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
