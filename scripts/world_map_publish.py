"""Publish a WORLD low-zoom PMTiles overview only after source + tile verification.

The current UK display-map and routing pointers are never modified. This package
contains global Natural Earth overview geometry only, not worldwide road legality.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

import publish_map_data as publisher
import world_sources
import world_tiles

ROOT = Path(__file__).resolve().parents[1]
TAG = "map-data-worldtiles"
POINTER = "worldtiles.json"
NAME = "world-overview.pmtiles"
RUN_TAG = re.compile(r"map-data-worldtiles-r[0-9]+-a[0-9]+")
PIPELINE_FILES = [
    "scripts/world_sources.py", "scripts/world_tiles.py",
    "scripts/world_map_publish.py", "schema/world-overview.yml",
    ".github/workflows/world-map-publish.yml",
]
ENGINE = "planetiler-0.10.1:5e5e7d8c4fc89b4573cc9109d0a543c3c2c798b2bcde7c3dcd2214031ee59c29"


def pipeline_hash(root=ROOT):
    digest = hashlib.sha256(ENGINE.encode("ascii"))
    for name in PIPELINE_FILES:
        f = Path(root) / name
        digest.update(name.encode("utf8") + b"\x00")
        digest.update(f.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def source_identity(source):
    source = world_sources.require_pointer(source)
    joined = {name: {"bytes": source["assets"][name]["bytes"],
                     "sha256": source["assets"][name]["sha256"],
                     "tag": source["assets"][name]["tag"]}
              for name in sorted(world_sources.SOURCES)}
    return joined


def validate_pointer(doc):
    if (not isinstance(doc, dict) or doc.get("schema") != 1 or
            doc.get("kind") != "low-zoom-world-overview-geometry-only" or
            doc.get("worldwide_routing") is not False or
            doc.get("android_app_map_selected") is not False):
        raise ValueError("Invalid world map release claim")
    m = doc.get("map") or {}
    if (not RUN_TAG.fullmatch(str(m.get("tag", ""))) or
            m.get("name") != NAME or m.get("layout") != "world-natural-earth-v1" or
            type(m.get("bytes")) is not int or not 4096 <= m["bytes"] <= world_tiles.MAX_TILES_BYTES or
            not re.fullmatch(r"[a-f0-9]{64}", str(m.get("sha256")))):
        raise ValueError("Invalid world PMTiles release identity")
    if not re.fullmatch(r"[a-f0-9]{64}", str(doc.get("pipeline_sha256", ""))):
        raise ValueError("Invalid world generation pipeline fingerprint")
    inputs = doc.get("source_assets")
    if not isinstance(inputs, dict) or set(inputs) != set(world_sources.SOURCES):
        raise ValueError("Missing world source assets")
    for name, v in inputs.items():
        if (not isinstance(v, dict) or type(v.get("bytes")) is not int or not 100 < v["bytes"] <= world_sources.MAX_SOURCE_BYTES or
                not re.fullmatch(r"[a-f0-9]{64}", str(v.get("sha256"))) or
                not re.fullmatch(r"map-data-world-overview-r[0-9]+-a[0-9]+", str(v.get("tag")))):
            raise ValueError("Untrusted world source identity")
    return doc


def read_pointer(github, folder):
    try:
        inventory = github.inventory(TAG)
    except publisher.GitHubError as e:
        if "HTTP 404" in str(e):
            return None
        raise
    if POINTER not in inventory:
        return None
    filename = github.download(TAG, POINTER, Path(folder))
    publisher.verify_inventory(inventory, {POINTER: publisher.metadata(filename, TAG)}, TAG)
    return validate_pointer(json.loads(filename.read_text()))


def is_due(source, current, pipeline):
    ident = source_identity(source)
    return current is None or current["source_assets"] != ident or current["pipeline_sha256"] != pipeline


def fetch_immutable_sources(github, source, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, ident in source_identity(source).items():
        path = github.download(ident["tag"], name, folder)
        if (path.stat().st_size != ident["bytes"] or
                publisher.digest(path) != ident["sha256"]):
            raise ValueError("Immutable world source asset failed its pinned SHA-256")
        world_sources.validate_geojson(name, path.read_bytes())
    return folder


def publish(github, archive, source, pipeline, run_tag, directory):
    """Keep the old pointer whenever a new candidate/upload/receipt is incomplete."""
    if not RUN_TAG.fullmatch(run_tag) or not re.fullmatch(r"[a-f0-9]{64}", pipeline):
        raise ValueError("Unsafe world output generation identity")
    checked = world_tiles.verify(archive)  # reads actual PMTiles/MVT tiles on six continents
    old = read_pointer(github, Path(directory) / "old-world")
    original = source_identity(source)
    size = Path(archive).stat().st_size
    sha = publisher.digest(archive)
    if old and not is_due(source, old, pipeline):
        # A redundant dispatched build did not change the source or generator.
        print("World map inputs unchanged; no new upload")
        return old, False
    if old and old["map"]["sha256"] == sha and old["map"]["bytes"] == size:
        # Changed observations/pipeline but identical rendered content: reuse
        # old immutable map bytes, only update the small manifest generation.
        immutable = old["map"]
        publisher.verify_inventory(github.inventory(immutable["tag"]),
                                   {NAME: {k: immutable[k] for k in ("tag", "bytes", "sha256")}},
                                   immutable["tag"])
        print("World PMTiles bytes unchanged; reuse existing release")
    else:
        publisher.upload_immutable(github, run_tag, {NAME: Path(archive)})
        expected = {NAME: publisher.metadata(archive, run_tag)}
        publisher.verify_inventory(github.inventory(run_tag), expected, run_tag, exact=True)
        immutable = {**expected[NAME], "name": NAME, "layout": "world-natural-earth-v1"}
    doc = validate_pointer({
        "schema": 1, "kind": "low-zoom-world-overview-geometry-only",
        "worldwide_routing": False, "android_app_map_selected": False,
        "source_assets": original, "pipeline_sha256": pipeline,
        "tested_sample_count": len(checked["samples"]),
        "map": immutable,
        "attribution": "Natural Earth public domain",
    })
    dest = Path(directory) / POINTER
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n")
    github.ensure_release(TAG)
    github.run("release", "upload", TAG, str(dest), "--repo", github.repo, "--clobber")
    publisher.verify_inventory(github.inventory(TAG),
                               {POINTER: publisher.metadata(dest, TAG)}, TAG)
    return doc, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--work", type=Path, default=Path("work/world-publish"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("due")
    commands.add_parser("fetch")
    p = commands.add_parser("publish")
    p.add_argument("--map", type=Path, required=True)
    opts = parser.parse_args()
    opts.work.mkdir(parents=True, exist_ok=True)
    gh = publisher.GitHub(opts.repo)
    if opts.command == "due":
        source = world_sources.read_pointer(gh, opts.work / "source-pointer")
        ready = source is not None and is_due(source, read_pointer(gh, opts.work / "world-pointer"), pipeline_hash())
        print("World overview publication " + ("due" if ready else "not due"))
        with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as output:
            output.write("due=" + str(ready).lower() + "\n")
    elif opts.command == "fetch":
        source = world_sources.read_pointer(gh, opts.work / "source-pointer")
        if source is None:
            raise ValueError("No verified worldwide source pointer")
        fetch_immutable_sources(gh, source, opts.work / "sources")
    else:
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise ValueError("World publication blocked outside map main")
        source = world_sources.read_pointer(gh, opts.work / "source-pointer")
        if source is None:
            raise ValueError("No verified worldwide source pointer")
        tag = f"{TAG}-r{int(os.environ['GITHUB_RUN_ID'])}-a{int(os.environ['GITHUB_RUN_ATTEMPT'])}"
        doc, changed = publish(gh, opts.map, source, pipeline_hash(), tag, opts.work)
        print(json.dumps({"promoted": changed, "map_tag": doc["map"]["tag"],
                          "sample_count": doc["tested_sample_count"]}))


if __name__ == "__main__":
    main()
