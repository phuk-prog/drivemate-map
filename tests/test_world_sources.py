"""Global source checks: geography, fail-closed identity and unchanged uploads."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import world_sources as world
import test_publish_map_data as fixtures


def feature(geometry, name):
    return {"type": "Feature", "properties": {"name": name}, "geometry": geometry}


def collection(features):
    return json.dumps({"type": "FeatureCollection", "features": features},
                      sort_keys=True).encode("utf-8")


def box(lon, lat):
    return {"type": "Polygon", "coordinates": [[
        [lon - 0.6, lat - 0.6], [lon + 0.6, lat - 0.6],
        [lon + 0.6, lat + 0.6], [lon - 0.6, lat + 0.6],
        [lon - 0.6, lat - 0.6]
    ]]}


def test_layers():
    land = [feature(box(lon, lat), name) for name, (lon, lat) in world.LAND_SAMPLES.items()]
    land *= 9
    countries = [feature(box(10.0, 40.0), f"country-{i}") for i in range(180)]
    places = [feature({"type": "Point", "coordinates": [20.0, 30.0]}, f"place-{i}")
              for i in range(120)]
    return {
        "world-land.geojson": collection(land),
        "world-countries.geojson": collection(countries),
        "world-places.geojson": collection(places),
    }


class InMemoryGitHub(fixtures.FakeGitHub):
    repo = "phuk-prog/drivemate-map"

    def inventory(self, tag):
        if tag not in self.files:
            raise world.publisher.GitHubError("HTTP 404: release not found")
        return super().inventory(tag)

    def run(self, *args):
        if args[:2] != ("release", "upload") or "--clobber" not in args:
            raise AssertionError("Only the mutable world pointer may be replaced")
        tag, path = args[2], Path(args[3])
        if tag != world.POINTER_TAG or path.name != world.POINTER:
            raise AssertionError("Unexpected pointer modification")
        self.actions.append(("pointer", tag, path.name))
        self.files.setdefault(tag, {})[path.name] = path.read_bytes()


class WorldSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.github = InMemoryGitHub()
        self.layers = test_layers()
        self.stats = {}
        for name, raw in self.layers.items():
            (self.out / name).write_bytes(raw)
            self.stats[name] = world.validate_geojson(name, raw)

    def test_six_continents_present_and_exact_immutable_source_bytes(self):
        self.assertEqual(3, len(self.stats))
        for name, result in self.stats.items():
            self.assertEqual(len(self.layers[name]), result["bytes"])
            self.assertEqual(64, len(result["sha256"]))

    def test_missing_continent_invalid_geometry_and_small_region_not_worldwide(self):
        absent = [feature(box(lon, lat), n) for n, (lon, lat) in
                  list(world.LAND_SAMPLES.items())[:-1]]
        with self.assertRaisesRegex(ValueError, "misses continents"):
            world.validate_geojson("world-land.geojson", collection(absent * 11))
        damaged = bytearray(self.layers["world-countries.geojson"])
        with self.assertRaises(Exception):
            world.validate_geojson("world-countries.geojson", bytes(damaged[:200]))

    def test_build_rejects_changed_source_after_initial_probe(self):
        (self.out / "world-land.geojson").write_bytes(self.layers["world-land.geojson"] + b" ")
        with self.assertRaisesRegex(ValueError, "Source changed after first verification"):
            world.publish(self.github, self.out, self.stats, "map-data-world-overview-r1-a1")
        self.assertNotIn(world.POINTER_TAG, self.github.files)

    def test_first_upload_then_exactly_identical_bytes_reuse_old_snapshot(self):
        first, changed = world.publish(self.github, self.out, self.stats,
                                       "map-data-world-overview-r1-a1")
        self.assertTrue(changed)
        self.assertFalse(first["rendered_world_pmtiles"])
        before = self.github.files[world.POINTER_TAG][world.POINTER]
        self.github.actions.clear()
        second, changed = world.publish(self.github, self.out, self.stats,
                                        "map-data-world-overview-r2-a1")
        self.assertFalse(changed)
        self.assertEqual(first, second)
        self.assertEqual(before, self.github.files[world.POINTER_TAG][world.POINTER])
        self.assertNotIn("map-data-world-overview-r2-a1", self.github.files)
        self.assertFalse(any(a[0] in ("pointer", "upload") for a in self.github.actions))

    def test_one_real_change_uploads_new_source_generation(self):
        first, _ = world.publish(self.github, self.out, self.stats,
                                 "map-data-world-overview-r1-a1")
        new_data = self.layers["world-places.geojson"].replace(b"place-0", b"place-X")
        (self.out / "world-places.geojson").write_bytes(new_data)
        self.stats["world-places.geojson"] = world.validate_geojson("world-places.geojson", new_data)
        second, changed = world.publish(self.github, self.out, self.stats,
                                        "map-data-world-overview-r2-a1")
        self.assertTrue(changed)
        self.assertNotEqual(first["assets"]["world-places.geojson"]["sha256"],
                            second["assets"]["world-places.geojson"]["sha256"])
        self.assertEqual("map-data-world-overview-r2-a1",
                         second["assets"]["world-places.geojson"]["tag"])

    def test_failed_asset_upload_does_not_replace_last_good_pointer(self):
        world.publish(self.github, self.out, self.stats, "map-data-world-overview-r1-a1")
        before = self.github.files[world.POINTER_TAG][world.POINTER]
        new_data = self.layers["world-places.geojson"].replace(b"place-0", b"place-Y")
        (self.out / "world-places.geojson").write_bytes(new_data)
        self.stats["world-places.geojson"] = world.validate_geojson("world-places.geojson", new_data)
        self.github.fail_upload = ("map-data-world-overview-r2-a1", "world-places.geojson")
        with self.assertRaises(Exception):
            world.publish(self.github, self.out, self.stats, "map-data-world-overview-r2-a1")
        self.assertEqual(before, self.github.files[world.POINTER_TAG][world.POINTER])

    def test_unreadable_live_pointer_fails_closed(self):
        world.publish(self.github, self.out, self.stats, "map-data-world-overview-r1-a1")
        self.github.files[world.POINTER_TAG][world.POINTER] = b"not-json"
        with self.assertRaises(Exception):
            world.publish(self.github, self.out, self.stats, "map-data-world-overview-r2-a1")
        self.assertNotIn("map-data-world-overview-r2-a1", self.github.files)

    def test_no_source_package_may_claim_full_world_rendering_or_routing(self):
        doc, _ = world.publish(self.github, self.out, self.stats, "map-data-world-overview-r1-a1")
        for key in ("rendered_world_pmtiles", "worldwide_road_routing"):
            broken = dict(doc)
            broken[key] = True
            with self.assertRaises(ValueError):
                world.require_pointer(broken)

    def test_three_small_remote_sources_can_be_probed_without_publish(self):
        remote = {world.BASE + path: self.layers[name]
                  for name, (path, _, _) in world.SOURCES.items()}
        class Response:
            def __init__(self, raw):
                self.raw = raw
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return None
            def read(self, n):
                return self.raw[:n]
        with patch.object(world, "urlopen", side_effect=lambda url, timeout: Response(remote[url])):
            outcome = world.download_sources(self.out / "download", opener=world.urlopen)
        self.assertEqual(set(world.SOURCES), set(outcome))
        self.assertNotIn(world.POINTER_TAG, self.github.files)


if __name__ == "__main__":
    unittest.main()
