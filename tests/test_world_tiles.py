"""Low-zoom world-map candidate preparation and safety contracts."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import world_tiles
from test_world_sources import test_layers


class WorldTileCandidateTests(unittest.TestCase):
    def test_world_schema_has_appropriate_geojson_sources_and_no_fake_roads(self):
        schema = yaml.safe_load((ROOT / "schema/world-overview.yml").read_text())
        self.assertEqual("Natural Earth (public domain)", schema["attribution"])
        sources = schema["sources"]
        self.assertEqual({"world_land", "world_countries", "world_places"}, set(sources))
        self.assertTrue(all(s["type"] == "geojson" for s in sources.values()))
        layers = {layer["id"] for layer in schema["layers"]}
        self.assertEqual(layers, {"world_land", "world_borders", "world_places"})
        self.assertNotIn("roads", str(schema))
        self.assertEqual(5, schema["args"]["maxzoom"])

    def test_small_synthetic_source_prepared_with_verifiable_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            source = p / "source"
            source.mkdir()
            for name, data in test_layers().items():
                (source / name).write_bytes(data)
            dest = p / "render"
            summary = world_tiles.prepare(source, dest)
            self.assertEqual({"world_land", "world_borders", "world_places"}, set(summary))
            for name in world_tiles.LAYERS:
                self.assertTrue((dest / name).is_file())
                doc = json.loads((dest / name).read_text())
                self.assertEqual("FeatureCollection", doc["type"])
            manifest = json.loads((dest / "world-build-inputs.json").read_text())
            self.assertEqual("natural-earth-small-scale-geometry-not-navigation", manifest["kind"])

    def test_meridian_and_polar_coordinate_clamping(self):
        self.assertEqual((0, 0), world_tiles.tile(0, -180, 80))
        self.assertEqual((0, 0), world_tiles.tile(0, 180, -80))
        self.assertEqual((7, 4), world_tiles.tile(3, 180, 0))
        self.assertEqual((0, 4), world_tiles.tile(3, -180, 0))
        for lon, lat in ((180, -90), (-180, 90), (0, 0)):
            x, y = world_tiles.tile(5, lon, lat)
            self.assertTrue(0 <= x < 32 and 0 <= y < 32)

    def test_cannot_claim_success_without_real_pmtiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                world_tiles.verify(Path(tmp) / "absent.pmtiles")


if __name__ == "__main__":
    unittest.main()
