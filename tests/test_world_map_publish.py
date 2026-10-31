"""The world map publishes only verified changes and never replaces UK assets."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import world_map_publish as worldmap
import world_sources as world
from test_world_sources import InMemoryGitHub, test_layers


class Publisher(InMemoryGitHub):
    def run(self, *args):
        if args[:2] != ("release", "upload") or "--clobber" not in args:
            raise AssertionError("World data may only overwrite a mutable pointer")
        tag, file = args[2], Path(args[3])
        if (tag, file.name) not in ((world.POINTER_TAG, world.POINTER),
                                    (worldmap.TAG, worldmap.POINTER)):
            raise AssertionError("Unexpected map publication")
        self.actions.append(("pointer", tag, file.name))
        self.files.setdefault(tag, {})[file.name] = file.read_bytes()


class WorldMapPublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.github = Publisher()
        self.sha = "b" * 64
        self.snapshot = {}
        for name, raw in test_layers().items():
            path = self.folder / name
            path.write_bytes(raw)
            self.snapshot[name] = world.validate_geojson(name, raw)
        self.source, _ = world.publish(
            self.github, self.folder, self.snapshot, "map-data-world-overview-r1-a1")
        self.archive = self.folder / worldmap.NAME
        self.archive.write_bytes(b"PMTiles\x03" + b"v" * 5200)
        self.verify = patch.object(worldmap.world_tiles, "verify",
                                   return_value={"samples": {
                                       "north-america": {}, "south-america": {}, "europe": {},
                                       "africa": {}, "asia": {}, "australia": {}}})
        self.verify.start()
        self.addCleanup(self.verify.stop)

    def test_unchanged_source_and_pipeline_does_not_build_or_upload(self):
        self.assertTrue(worldmap.is_due(self.source, None, self.sha))
        first, changed = worldmap.publish(self.github, self.archive, self.source, self.sha,
                                           "map-data-worldtiles-r1-a1", self.folder)
        self.assertTrue(changed)
        self.assertFalse(first["android_app_map_selected"])
        self.assertFalse(first["worldwide_routing"])
        self.assertFalse(worldmap.is_due(self.source, first, self.sha))
        before = self.github.files[worldmap.TAG][worldmap.POINTER]
        self.github.actions.clear()
        second, changed = worldmap.publish(self.github, self.archive, self.source, self.sha,
                                            "map-data-worldtiles-r2-a1", self.folder)
        self.assertFalse(changed)
        self.assertEqual(before, self.github.files[worldmap.TAG][worldmap.POINTER])
        self.assertNotIn("map-data-worldtiles-r2-a1", self.github.files)
        self.assertFalse(any(x[0] in ("upload", "pointer") for x in self.github.actions))

    def test_changed_pipeline_identical_tile_bytes_reuse_immutable_map(self):
        original, _ = worldmap.publish(self.github, self.archive, self.source, self.sha,
                                        "map-data-worldtiles-r1-a1", self.folder)
        new, changed = worldmap.publish(self.github, self.archive, self.source, "a" * 64,
                                         "map-data-worldtiles-r2-a1", self.folder)
        self.assertTrue(changed, "Small source/recipe receipt can advance without binary upload")
        self.assertEqual(original["map"], new["map"])
        self.assertNotIn("map-data-worldtiles-r2-a1", self.github.files)

    def test_changed_tiles_update_immutable_map_then_pointer(self):
        original, _ = worldmap.publish(self.github, self.archive, self.source, self.sha,
                                        "map-data-worldtiles-r1-a1", self.folder)
        self.archive.write_bytes(b"PMTiles\x03" + b"z" * 5201)
        next_doc, changed = worldmap.publish(self.github, self.archive, self.source,
            "c" * 64, "map-data-worldtiles-r2-a1", self.folder)
        self.assertTrue(changed)
        self.assertNotEqual(original["map"]["sha256"], next_doc["map"]["sha256"])
        self.assertEqual("map-data-worldtiles-r2-a1", next_doc["map"]["tag"])

    def test_failed_new_upload_keeps_old_world_pointer_and_uk_map(self):
        worldmap.publish(self.github, self.archive, self.source, self.sha,
                         "map-data-worldtiles-r1-a1", self.folder)
        before = self.github.files[worldmap.TAG][worldmap.POINTER]
        self.archive.write_bytes(b"PMTiles\x03" + b"y" * 5202)
        self.github.fail_upload = ("map-data-worldtiles-r2-a1", worldmap.NAME)
        with self.assertRaises(Exception):
            worldmap.publish(self.github, self.archive, self.source, "c" * 64,
                             "map-data-worldtiles-r2-a1", self.folder)
        self.assertEqual(before, self.github.files[worldmap.TAG][worldmap.POINTER])
        self.assertNotIn("map-data-ukmap", self.github.files)

    def test_sources_are_downloaded_from_pinned_release_and_checksum_verified(self):
        downloaded = worldmap.fetch_immutable_sources(self.github, self.source,
                                                      self.folder / "sources")
        self.assertEqual(3, len(list(downloaded.glob("*.geojson"))))
        remote = self.source["assets"]["world-land.geojson"]
        self.github.downloads[(remote["tag"], "world-land.geojson")] = b"unexpected update"
        with self.assertRaisesRegex(ValueError, "pinned SHA-256"):
            worldmap.fetch_immutable_sources(self.github, self.source,
                                              self.folder / "altered")

    def test_error_in_existing_pointer_blocks_publication(self):
        worldmap.publish(self.github, self.archive, self.source, self.sha,
                         "map-data-worldtiles-r1-a1", self.folder)
        self.github.files[worldmap.TAG][worldmap.POINTER] = b"not-json"
        self.archive.write_bytes(b"PMTiles\x03" + b"z" * 5300)
        with self.assertRaises(Exception):
            worldmap.publish(self.github, self.archive, self.source, "e" * 64,
                             "map-data-worldtiles-r2-a1", self.folder)
        self.assertNotIn("map-data-worldtiles-r2-a1", self.github.files)

    def test_pointer_cannot_reuse_missing_immutable_world_map(self):
        """A valid rolling JSON receipt is not proof its referenced archive still exists."""
        doc, _ = worldmap.publish(self.github, self.archive, self.source, self.sha,
                                  "map-data-worldtiles-r1-a1", self.folder)
        del self.github.files[doc["map"]["tag"]][worldmap.NAME]
        with self.assertRaisesRegex(ValueError, "Asset verification failed"):
            worldmap.read_pointer(self.github, self.folder / "missing-immutable")

    def test_world_tiles_pointer_rejects_missing_immutable_source_asset(self):
        """A verified world PMTiles pointer must not outlive its pinned source assets."""
        doc, _ = worldmap.publish(self.github, self.archive, self.source, self.sha,
                                  "map-data-worldtiles-r1-a1", self.folder)
        source = doc["source_assets"]["world-places.geojson"]
        del self.github.files[source["tag"]]["world-places.geojson"]
        with self.assertRaisesRegex(ValueError, "Asset verification failed"):
            worldmap.read_pointer(self.github, self.folder / "missing-world-source")

    def test_world_tiles_pointer_rejects_mixed_source_release_generations(self):
        doc, _ = worldmap.publish(self.github, self.archive, self.source, self.sha,
                                  "map-data-worldtiles-r1-a1", self.folder)
        mixed = copy.deepcopy(doc)
        mixed["source_assets"]["world-places.geojson"]["tag"] = "map-data-world-overview-r2-a1"
        with self.assertRaisesRegex(ValueError, "one immutable release"):
            worldmap.validate_pointer(mixed)
        self.assertEqual(doc, worldmap.validate_pointer(doc))

    def test_world_tiles_pointer_requires_natural_earth_attribution(self):
        doc, _ = worldmap.publish(self.github, self.archive, self.source, self.sha,
                                  "map-data-worldtiles-r1-a1", self.folder)
        for invalid in ("Proprietary", "", None):
            broken = copy.deepcopy(doc)
            broken["attribution"] = invalid
            with self.assertRaisesRegex(ValueError, "attribution"):
                worldmap.validate_pointer(broken)
        self.assertEqual(doc, worldmap.validate_pointer(doc))

    def test_world_tiles_pointer_requires_six_continent_sample_proof(self):
        doc, _ = worldmap.publish(self.github, self.archive, self.source, self.sha,
                                  "map-data-worldtiles-r1-a1", self.folder)
        for invalid in (0, 5, 7, "6", None):
            broken = copy.deepcopy(doc)
            broken["tested_sample_count"] = invalid
            with self.assertRaisesRegex(ValueError, "six-continent"):
                worldmap.validate_pointer(broken)
        self.assertEqual(6, doc["tested_sample_count"])
        self.assertEqual(doc, worldmap.validate_pointer(doc))

    def test_six_world_samples_must_be_the_six_required_continents(self):
        """A six-entry receipt from the wrong places cannot authorize a world release."""
        unrelated = {f"unrelated-{i}": {"land_features": 1} for i in range(6)}
        with patch.object(worldmap.world_tiles, "verify", return_value={"samples": unrelated}):
            with self.assertRaisesRegex(ValueError, "continental sample identities"):
                worldmap.publish(self.github, self.archive, self.source, self.sha,
                                 "map-data-worldtiles-r1-a1", self.folder)
        self.assertNotIn(worldmap.TAG, self.github.files,
                         "A failed sample-identity gate must not upload world tiles or pointer")

    def test_source_and_world_release_types_cannot_claim_routing(self):
        doc, _ = worldmap.publish(self.github, self.archive, self.source, self.sha,
                                  "map-data-worldtiles-r1-a1", self.folder)
        for attr in ("android_app_map_selected", "worldwide_routing"):
            broken = copy.deepcopy(doc)
            broken[attr] = True
            with self.assertRaises(ValueError):
                worldmap.validate_pointer(broken)


if __name__ == "__main__":
    unittest.main()
