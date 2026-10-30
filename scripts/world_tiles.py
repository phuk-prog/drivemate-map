"""Build inputs and independently validate candidate WORLD low-zoom PMTiles.

Only a temporary prototype from public-domain Natural Earth source data.
The current UK basemap is NOT replaced; the world geometry has no turn/lane
legality, local street detail, high-zoom road graph, or worldwide offline routing.
"""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

from shapely.geometry import shape, mapping, Polygon, MultiPolygon
from shapely.validation import make_valid

import world_sources

# Every candidate must have visible land geometry in six independent continental
# city tiles, not merely a synthetic global bounds rectangle or ocean polygon.
SAMPLES = world_sources.LAND_SAMPLES
ZOOM = 3
MAX_TILES_BYTES = 100_000_000
LAYERS = {
    "world-land.geojson": "world_land",
    "world-countries.geojson": "world_borders",
    "world-places.geojson": "world_places",
}


def polygons(geometry):
    """Keep actual polygon parts after repairing known Natural Earth self-intersections."""
    valid = make_valid(geometry)
    if isinstance(valid, (Polygon, MultiPolygon)):
        return valid
    polys = [g for g in getattr(valid, "geoms", ()) if isinstance(g, (Polygon, MultiPolygon))]
    if not polys:
        raise ValueError("Natural Earth geometry cannot be represented as valid land polygons")
    from shapely.ops import unary_union
    result = unary_union(polys)
    if result.is_empty or not result.is_valid:
        raise ValueError("Repaired world geometry still invalid")
    return result


def prepare(source_dir, dest_dir):
    """Preserve source identities, repair topology only in the rendered candidate."""
    source_dir, dest_dir = Path(source_dir), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    summaries = {}
    for name, layer in LAYERS.items():
        src = source_dir / name
        raw = src.read_bytes()
        checked = world_sources.validate_geojson(name, raw)
        document = json.loads(raw)
        repaired = 0
        prepared = []
        for feature in document["features"]:
            geo = shape(feature["geometry"])
            if name == "world-places.geojson":
                if geo.geom_type not in ("Point", "MultiPoint"):
                    raise ValueError("Invalid world place geometry")
                target = geo
            else:
                if geo.geom_type not in ("Polygon", "MultiPolygon"):
                    raise ValueError("Invalid world polygon source")
                target = polygons(geo)
            if not geo.is_valid:
                repaired += 1
            original = feature.get("properties") or {}
            if not isinstance(original, dict):
                raise ValueError("Untrusted world property collection")
            label = str(original.get("NAME") or original.get("NAMEASCII") or original.get("name") or "").strip()
            properties = {"kind": "land" if layer == "world_land" else
                          "border" if layer == "world_borders" else "place"}
            if layer != "world_land" and label:
                properties["name"] = label[:160]
            if layer == "world_places":
                population = original.get("POP_MAX", 0)
                if isinstance(population, (int, float)) and math.isfinite(population) and population >= 0:
                    properties["population"] = int(min(population, 10_000_000_000))
            prepared.append({"type": "Feature", "properties": properties, "geometry": mapping(target)})
        result = {"type": "FeatureCollection", "features": prepared}
        out = dest_dir / name
        out.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n")
        summaries[layer] = {
            "original_sha256": checked["sha256"],
            "original_features": checked["features"],
            "rendered_features": len(prepared),
            "repaired_geometries": repaired,
            "prepared_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        }
    (dest_dir / "world-build-inputs.json").write_text(
        json.dumps({"schema": 1, "kind": "natural-earth-small-scale-geometry-not-navigation",
                    "layers": summaries}, sort_keys=True, indent=2) + "\n")
    return summaries


def tile(z, lon, lat):
    lat = max(-85.051128, min(85.051128, lat))
    n = 1 << z
    x = min(n - 1, max(0, int((lon + 180.0) / 360.0 * n)))
    y = min(n - 1, max(0, int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)))
    return x, y


def verify(archive):
    """Read genuine MVT features from PMTiles at six distant land viewports."""
    from pmtiles.reader import Reader, MmapSource
    from mapbox_vector_tile import decode
    p = Path(archive)
    if not p.is_file() or not 4_096 <= p.stat().st_size <= MAX_TILES_BYTES:
        raise ValueError("World overview candidate archive is missing/oversized")
    regions = {}
    with p.open("rb") as stream:
        reader = Reader(MmapSource(stream))
        for label, (lon, lat) in SAMPLES.items():
            x, y = tile(ZOOM, lon, lat)
            payload = reader.get(ZOOM, x, y)
            if not payload:
                raise ValueError("World overview has missing tile near " + label)
            if payload[:2] == b"\x1f\x8b":
                payload = gzip.decompress(payload)
            layers = decode(payload)
            count = len(layers.get("world_land", {}).get("features", ()))
            if count <= 0:
                raise ValueError("World PMTiles has no land shapes near " + label)
            regions[label] = {"x": x, "y": y, "land_features": count}
    return {"schema": 1, "scope": "six-continental-low-zoom-map-tiles-not-world-navigation",
            "file_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "bytes": p.stat().st_size, "zoom": ZOOM, "samples": regions,
            "rendered_map_in_android": False, "worldwide_road_routing": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify(args.verify), indent=2, sort_keys=True))
    else:
        if args.source_dir is None or args.output_dir is None:
            parser.error("--source-dir and --output-dir are required for preparation")
        print(json.dumps(prepare(args.source_dir, args.output_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
