"""Source-backed, change-only Natural Earth WORLD overview data snapshots.

Provides small public-domain global land, country and named-place GeoJSON files.
This is a verified *source package*, not rendered PMTiles or worldwide routing.
It never touches the established UK PMTiles pointer or its routing datasets.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.request import urlopen

from shapely.geometry import Point, shape
from shapely.validation import make_valid

import publish_map_data as publisher

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
SOURCES = {
    "world-land.geojson": ("ne_110m_land.geojson", 50, "land"),
    "world-countries.geojson": ("ne_110m_admin_0_countries.geojson", 170, "countries"),
    "world-places.geojson": ("ne_110m_populated_places.geojson", 100, "places"),
}
POINTER_TAG = "map-data-world-overview"
POINTER = "world.json"
MAX_SOURCE_BYTES = 5_000_000
# Deliberately deep inside the continental land masses. This verifies global,
# not country-level, coverage and makes no claim about local navigation or roads.
LAND_SAMPLES = {
    "north-america": (-104.9903, 39.7392),
    "south-america": (-47.8828, -15.7939),
    "europe": (2.3522, 48.8566),
    "africa": (36.8219, -1.2921),
    "asia": (77.2090, 28.6139),
    "australia": (151.2093, -33.8688),
}


def validate_geojson(name, raw):
    if name not in SOURCES or not isinstance(raw, bytes) or not 100 < len(raw) <= MAX_SOURCE_BYTES:
        raise ValueError("Unknown, empty or excessive world source")
    doc = json.loads(raw)
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        raise ValueError("World source must be a GeoJSON FeatureCollection")
    features = doc.get("features")
    if not isinstance(features, list) or len(features) < SOURCES[name][1]:
        raise ValueError("Incomplete world GeoJSON feature collection")
    geometries = []
    repairable_invalid_count = 0
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError("Malformed world feature")
        geom = feature.get("geometry")
        if not isinstance(geom, dict) or geom.get("type") not in {
                "Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}:
            raise ValueError("Malformed world geometry")
        candidate = shape(geom)
        if candidate.is_empty:
            raise ValueError("Empty world geometry")
        # Generalised public-domain coastlines can have self-intersections.
        # Reject unrepairable shapes; accept repaired topology ONLY in the
        # coverage probe. Retain the ORIGINAL upstream bytes, and explicitly
        # mark that a future PMTiles generator must repair before rendering.
        if not candidate.is_valid:
            candidate = make_valid(candidate)
            if candidate.is_empty or not candidate.is_valid:
                raise ValueError("Unrepairable Natural Earth geometry")
            repairable_invalid_count += 1
        west, south, east, north = candidate.bounds
        if not (-180.000001 <= west <= east <= 180.000001 and
                -90.000001 <= south <= north <= 90.000001):
            raise ValueError("World source outside geographic coordinate bounds")
        geometries.append(candidate)
    if name == "world-land.geojson":
        absent = [label for label, (lon, lat) in LAND_SAMPLES.items()
                  if not any(geometry.covers(Point(lon, lat)) for geometry in geometries)]
        if absent:
            raise ValueError("Worldwide land source misses continents: " + ",".join(absent))
    return {"features": len(features), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "topology_repairs_required_for_future_render": repairable_invalid_count}


def download_sources(directory, opener=urlopen):
    """Limit source sizes, validate data and retain one immutable run snapshot."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    summaries = {}
    for name, (remote, _, kind) in sorted(SOURCES.items()):
        url = BASE + remote
        with opener(url, timeout=90) as response:
            raw = response.read(MAX_SOURCE_BYTES + 1)
        info = validate_geojson(name, raw)
        path = directory / name
        path.write_bytes(raw)
        summaries[name] = {**info, "source": url, "type": kind,
                           "licence": "Natural Earth public domain"}
    return summaries


def require_pointer(doc):
    """Reader contract prevents interpreting the source snapshot as a road map."""
    if (not isinstance(doc, dict) or type(doc.get("schema")) is not int or doc["schema"] != 1 or
            doc.get("kind") != "natural-earth-world-overview-source-snapshot" or
            doc.get("rendered_world_pmtiles") is not False or
            doc.get("worldwide_road_routing") is not False):
        raise ValueError("World source manifest is invalid or overclaims readiness")
    # A well-formed hash inventory must not launder an unrelated or proprietary
    # source into the Natural Earth public-domain worldwide overview contract.
    if (doc.get("source") != "Natural Earth (public domain)" or
            doc.get("source_index") != "https://www.naturalearthdata.com/downloads/"):
        raise ValueError("Invalid Natural Earth source provenance")
    assets = doc.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(SOURCES):
        raise ValueError("Incomplete world overview assets")
    for name, item in assets.items():
        if (not isinstance(item, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256"))) or
                type(item.get("bytes")) is not int or not 100 < item["bytes"] <= MAX_SOURCE_BYTES or
                not re.fullmatch(r"map-data-world-overview-r[0-9]+-a[0-9]+", str(item.get("tag")))):
            raise ValueError("Invalid world overview asset identity")
    if len({item["tag"] for item in assets.values()}) != 1:
        raise ValueError("World sources must share one immutable release")
    return doc


def read_pointer(github, directory):
    try:
        listing = github.inventory(POINTER_TAG)
    except publisher.GitHubError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise
    if POINTER not in listing:
        return None
    src = github.download(POINTER_TAG, POINTER, Path(directory))
    publisher.verify_inventory(listing, {POINTER: publisher.metadata(src, POINTER_TAG)}, POINTER_TAG)
    doc = require_pointer(json.loads(src.read_text()))
    # A rolling source receipt must not outlive the immutable GeoJSON files it
    # names. Check all three GitHub asset digests/sizes before returning a
    # reusable generation; never silently treat a broken pointer as current.
    tag = next(iter(doc["assets"].values()))["tag"]
    expected = {
        name: {key: doc["assets"][name][key] for key in ("tag", "bytes", "sha256")}
        for name in SOURCES
    }
    publisher.verify_inventory(github.inventory(tag), expected, tag, exact=True)
    return doc


def publish(github, directory, summaries, tag):
    """Only changed GeoJSON bytes cause publication; old pointers remain on error."""
    directory = Path(directory)
    if set(summaries) != set(SOURCES):
        raise ValueError("Incomplete source snapshot")
    for name in SOURCES:
        data = (directory / name).read_bytes()
        checked = validate_geojson(name, data)
        if checked["sha256"] != summaries[name]["sha256"] or checked["bytes"] != summaries[name]["bytes"]:
            raise ValueError("Source changed after first verification")
    old = read_pointer(github, directory / "old-pointer")
    if old and all(old["assets"][name]["sha256"] == summaries[name]["sha256"] and
                   old["assets"][name]["bytes"] == summaries[name]["bytes"] for name in SOURCES):
        print("World source bytes unchanged; no release or pointer update")
        return old, False
    if not re.fullmatch(r"map-data-world-overview-r[0-9]+-a[0-9]+", tag):
        raise ValueError("Unsafe world release name")
    assets = {name: directory / name for name in SOURCES}
    publisher.upload_immutable(github, tag, assets)
    expected = {name: publisher.metadata(path, tag) for name, path in assets.items()}
    publisher.verify_inventory(github.inventory(tag), expected, tag, exact=True)
    doc = require_pointer({
        "schema": 1, "kind": "natural-earth-world-overview-source-snapshot",
        "rendered_world_pmtiles": False, "worldwide_road_routing": False,
        "source": "Natural Earth (public domain)",
        "source_index": "https://www.naturalearthdata.com/downloads/",
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "coverage": "worldwide-small-scale-geometry-only",
        "assets": {name: {**expected[name], "name": name} for name in SOURCES},
    })
    pointer = directory / POINTER
    pointer.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    github.ensure_release(POINTER_TAG)
    github.run("release", "upload", POINTER_TAG, str(pointer), "--repo", github.repo, "--clobber")
    publisher.verify_inventory(github.inventory(POINTER_TAG),
                               {POINTER: publisher.metadata(pointer, POINTER_TAG)}, POINTER_TAG)
    return doc, True


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--repo", required=True)
    args.add_argument("--work", type=Path, default=Path("work/world-source"))
    args.add_argument("--publish", action="store_true", help="Publish changed source data on main only")
    opts = args.parse_args()
    summaries = download_sources(opts.work)
    print(json.dumps({"world_source_check": "passed", "layers": summaries},
                     indent=2, sort_keys=True), flush=True)
    if opts.publish:
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise ValueError("World publication permitted only from map main")
        run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
        tag = "map-data-world-overview-r" + str(int(run_id)) + "-a" + str(int(attempt))
        _, changed = publish(publisher.GitHub(opts.repo), opts.work, summaries, tag)
        print("World source snapshot published: " + str(changed), flush=True)


if __name__ == "__main__":
    main()
