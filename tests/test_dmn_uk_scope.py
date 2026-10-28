"""Category 1: reject worldwide OSM change contamination before UK DMN packing.

Broad envelope is a fail-closed far-outlier check, not legal UK borders or
Northern Ireland polygon clipping. Based on run 40656223326 (-46, 170).
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "scripts/dmn_tiles.py"
SPEC = importlib.util.spec_from_file_location("_dmn_tiles_uk_scope", SOURCE)
dmn_tiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dmn_tiles)


class UkDmnSourceScope(unittest.TestCase):
    def test_far_outlier_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "DMN_UK_ENVELOPE_VIOLATION"):
            dmn_tiles.require_uk_road_squares({(53 * 4, -2 * 4), (-46 * 4, 170 * 4)})

    def test_uk_envelope_is_accepted_without_claiming_border_accuracy(self):
        self.assertEqual(3, len(dmn_tiles.require_uk_road_squares({
            (53 * 4, -2 * 4), (54 * 4, -5 * 4), (60 * 4, -1 * 4)})))
        # Isle of Man is inside the broad envelope, but is NOT part of the UK.
        self.assertEqual(1, len(dmn_tiles.require_uk_road_squares({(54 * 4, -4 * 4)})))

    def test_empty_source_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "DMN_UK_MAIN_ROADS_EMPTY"):
            dmn_tiles.require_uk_road_squares(set())

    def test_main_pack_is_not_written_before_scope_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(out=str(Path(tmp) / "out"),
                                   work=str(Path(tmp) / "work"),
                                   pbf="fixture.osm.pbf", dmn="dmn", source="fixture")
            with patch.object(dmn_tiles, "run"), patch.object(dmn_tiles, "road_squares",
                     return_value={(-46 * 4, 170 * 4)}), patch.object(dmn_tiles, "pack") as pack:
                with self.assertRaisesRegex(ValueError, "DMN_UK_ENVELOPE_VIOLATION"):
                    dmn_tiles.main_roads(args)
                pack.assert_not_called()


if __name__ == "__main__":
    unittest.main()
