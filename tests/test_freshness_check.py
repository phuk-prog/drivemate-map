"""The freshness check passes only on recorded facts."""
import hashlib
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import freshness_check as F  # noqa: E402

OURS = "8fd7fa119d35d18d330226a3ff08b498"
TAG = "map-data-20261015-r38688720753-a1"


def inputs(md5=OURS, dmn_tag=TAG + "-extra"):
    cp = json.dumps({"schema": 1, "change_signal": {"algorithm": "md5", "value": OURS}}).encode()
    manifest = {"files": {"source-checkpoint.json": {"sha256": hashlib.sha256(cp).hexdigest()}}}
    latest = {"tag": TAG, "extra_tag": TAG + "-extra"}
    return latest, manifest, cp, {"source": f"OSM snapshot {dmn_tag}"}, f"{md5}  united-kingdom-latest.osm.pbf\n"


def day(d):
    return datetime(2026, 10, d, 9, tzinfo=timezone.utc)


class Freshness(unittest.TestCase):
    def run_check(self, now, modified=day(15), **kw):
        return {name: ok for name, ok, _ in F.check(*inputs(**kw), modified, now)}

    def test_current_extract_passes(self):
        self.assertTrue(all(self.run_check(day(18)).values()))

    def test_newer_extract_within_a_week_passes(self):
        rows = self.run_check(day(18), modified=day(18), md5="0" * 32)
        self.assertTrue(rows["Same as OpenStreetMap now"])

    def test_more_than_eight_days_behind_fails(self):
        rows = self.run_check(day(25), modified=day(25), md5="0" * 32)
        self.assertFalse(rows["Same as OpenStreetMap now"])
        self.assertFalse(rows[f"Snapshot at most {F.MAX_DAYS} days old"])

    def test_road_pieces_from_another_snapshot_fail(self):
        rows = self.run_check(day(18), dmn_tag="map-data-20261008-r1-a1-extra")
        self.assertFalse(rows["DMN road pieces from the same snapshot"])

    def test_tampered_checkpoint_or_unreadable_md5_cannot_pass(self):
        latest, manifest, cp, dmn, md5 = inputs()
        with self.assertRaises(ValueError):
            F.check(latest, manifest, cp + b" ", dmn, md5, day(15), day(18))
        with self.assertRaises(ValueError):
            F.check(latest, manifest, cp, dmn, "<html>busy</html>", day(15), day(18))
        with self.assertRaises(ValueError):
            F.check({**latest, "tag": "map-data-x"}, manifest, cp, dmn, md5, day(15), day(18))


if __name__ == "__main__":
    unittest.main()
