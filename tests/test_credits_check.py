"""Owners' published wording is required, near misses are caught."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import credits_check as C  # noqa: E402


class Credits(unittest.TestCase):
    def test_exact_wording_needed(self):
        self.assertEqual(C.missing("Contains OS data © Crown copyright and database right 2026", "OS Open Names"), [])
        for near in ("Contains OS data © Crown Copyright and database right 2026",
                     "Contains OS data © Crown copyright and database rights 2026", "OS data"):
            with self.subTest(near=near):
                self.assertTrue(C.missing(near, "OS Open Names"))

    def test_one_of_and_kotlin_joins(self):
        text = '"© OpenStreetMap contributors, " +\n    "ODbL https://www.openstreetmap.org/copyright"'
        self.assertEqual(C.missing(text, "OpenStreetMap"), [])
        self.assertEqual(C.missing("OpenStreetMap openstreetmap.org/copyright", "OpenStreetMap"), ["ODbL or Open Database Licence"])

    def test_report_counts_each_file(self):
        lines, fails = C.check({"a": "Powered by TfL Open Data", "b": "TfL"}, ["Transport for London"])
        self.assertEqual(fails, 1)
        self.assertIn('b lacks: "Powered by TfL Open Data"', lines[1])


if __name__ == "__main__":
    unittest.main()
