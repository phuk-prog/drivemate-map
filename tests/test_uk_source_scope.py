"""Category 1: frozen national source-scope regression tests.

The upstream Geofabrik United Kingdom PBF already includes Northern Ireland
but not the Isle of Man or Channel Islands. Applying planet-wide hourly diffs
must be followed by an explicit UK polygon extract before either producer uses
the result. These checks are source/command contracts, not legal border proof.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import uk_map


class UKSourceScopeTests(unittest.TestCase):
    def test_both_national_producers_exclude_crown_dependencies(self):
        for name in ("dmn-pack.yml", "uk-map.yml"):
            with self.subTest(workflow=name):
                source = (ROOT / ".github/workflows" / name).read_text()
                self.assertIn("europe/united-kingdom", source)
                self.assertNotIn("europe/isle-of-man", source)
                self.assertNotIn("europe/guernsey-jersey", source)

    def test_both_national_producers_fetch_polygon_and_pass_it_to_clipper(self):
        for name in ("dmn-pack.yml", "uk-map.yml"):
            with self.subTest(workflow=name):
                source = (ROOT / ".github/workflows" / name).read_text()
                self.assertIn("united-kingdom.poly", source)
                self.assertIn("--polygon work/uk-source.poly", source)

    def test_missing_or_empty_polygon_fails_before_osmium(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            called = []
            run = lambda *a, **k: called.append((a, k))
            for polygon in (root / "missing.poly", root / "empty.poly"):
                if polygon.name == "empty.poly":
                    polygon.write_bytes(b"")
                with self.subTest(polygon=polygon.name), self.assertRaises(ValueError):
                    uk_map.clip_uk_source(root / "source.osm.pbf", polygon, root / "out.osm.pbf", run=run)
            self.assertEqual([], called)

    def test_clipping_preserves_complete_cross_border_ways(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            polygon = root / "uk.poly"
            polygon.write_text("UK\\n1\\n  -2 53\\n  -2 54\\n  -1 54\\nEND\\nEND\\n")
            calls = []
            def fake_run(command, *, check):
                calls.append((command, check))
                (root / "out.osm.pbf").write_bytes(b"result")
            output = uk_map.clip_uk_source(root / "updated.osm.pbf", polygon, root / "out.osm.pbf", run=fake_run)
            self.assertEqual(root / "out.osm.pbf", output)
            self.assertEqual(1, len(calls))
            command, check = calls[0]
            self.assertEqual(["osmium", "extract", "--polygon", str(polygon), "-s", "complete_ways"], command[:7])
            self.assertIn(str(root / "updated.osm.pbf"), command)
            self.assertTrue(check)

    def test_osmium_clip_error_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            polygon = root / "uk.poly"
            polygon.write_bytes(b"polygon")
            def fail(command, *, check):
                raise subprocess.CalledProcessError(1, command)
            with self.assertRaises(subprocess.CalledProcessError):
                uk_map.clip_uk_source(root / "updated.osm.pbf", polygon, root / "out.osm.pbf", run=fail)
            self.assertFalse((root / "out.osm.pbf").exists())


if __name__ == "__main__":
    unittest.main()
