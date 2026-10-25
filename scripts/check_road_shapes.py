"""Measure named or numbered road bends against the archived OSM vertices.

Compare both maps on the same snapshot, at every driving zoom, including overzoom.
Distances are logical screen pixels on MapLibre's 512-pixel world tiles, independent
of display density. This measures fidelity to mapped bends, not invented splines.
Reference extracts are real OSM; synthetic lines are used only to test this reader.
"""
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ZOOMS = (12, 13, 14, 15, 16, 17, 17.8)
KINDS = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'minor', 'service'}


def world(lon, lat):
    return ((lon + 180) / 360,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2)


def distances(points, segments):
    if not segments:
        raise ValueError('No road geometry to compare')
    result = []
    for x, y in points:
        best = math.inf
        for (ax, ay), (bx, by) in segments:
            dx, dy = bx - ax, by - ay
            length = dx * dx + dy * dy
            t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / length)) if length else 0
            best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
        result.append(best)
    return result


def p95(values):
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def no_worse(old, new):
    return bool(old) and len(old) == len(new) and p95(new) <= p95(old) + 1e-9


def road_kind(highway):
    highway = highway.removesuffix('_link')
    return 'minor' if highway in ('residential', 'unclassified', 'living_street', 'road') else highway


def references(directory, area):
    grouped = defaultdict(list)
    path = directory / (area['id'] + '.geojsonseq')
    for line in path.read_text().splitlines():
        feature = json.loads(line.lstrip('\x1e'))
        p, g = feature['properties'], feature['geometry']
        kind, name = road_kind(p.get('highway', '')), p.get('name') or p.get('ref')
        if kind not in KINDS or not name or p.get('area') == 'yes' or g['type'] != 'LineString':
            continue
        points = g['coordinates']
        if len(points) < 3:
            continue  # use mapped bends rather than two-point straight lines
        for lon, lat, *_ in points:
            if abs(lat - area['lat']) <= 0.004 and abs(lon - area['lon']) <= 0.007:
                grouped[kind].append(world(lon, lat))
    if not grouped:
        raise ValueError('No named or numbered mapped bends in ' + area['id'])
    return grouped


def tile_roads(reader, area, zoom):
    from mapbox_vector_tile import decode
    tile_zoom = min(math.floor(zoom), reader.header()['max_zoom'])
    n = 2 ** tile_zoom
    cx, cy = world(area['lon'], area['lat'])
    cx, cy = int(cx * n), int(cy * n)
    grouped = defaultdict(list)
    for x in range(cx - 1, cx + 2):
        for y in range(cy - 1, cy + 2):
            data = reader.get(tile_zoom, x, y)
            if not data:
                continue
            if data[:2] == b'\x1f\x8b':
                data = gzip.decompress(data)
            layers = decode(data, default_options={'y_coord_down': True})
            for name in ('roads', 'transportation'):
                layer = layers.get(name)
                if not layer:
                    continue
                extent = layer['extent']
                for feature in layer['features']:
                    p, g = feature['properties'], feature['geometry']
                    kind = p.get('kind', p.get('class'))
                    if kind not in KINDS:
                        continue
                    lines = [g['coordinates']] if g['type'] == 'LineString' else g['coordinates'] if g['type'] == 'MultiLineString' else []
                    for line in lines:
                        points = [((x + px / extent) / n, (y + py / extent) / n) for px, py in line]
                        grouped[kind].extend(zip(points, points[1:]))
    return grouped


def compare(old, new, directory):
    from pmtiles.reader import MmapSource, Reader
    failures = []
    print('## Bend error against our own archived OSM\n')
    print('Named or numbered mapped bends at six UK places; p95 distance in logical screen pixels.\n'
          'This is a comparison with the old map, not a universal smoothness threshold.\n')
    print('| Place | Driving zoom | OSM vertices compared | Old p95 px | New p95 px | Result |\n|---|---|---|---|---|---|')
    areas = json.loads((directory / 'areas.json').read_text())
    if not areas:
        raise ValueError('No real-map reference areas')
    with open(old, 'rb') as old_file, open(new, 'rb') as new_file:
        readers = [Reader(MmapSource(old_file)), Reader(MmapSource(new_file))]
        for area in areas:
            refs = references(directory, area)
            for zoom in ZOOMS:
                maps = [tile_roads(reader, area, zoom) for reader in readers]
                errors = [[], []]
                for key, points in refs.items():
                    if key not in maps[0]:
                        continue  # this road type was not drawn by the old map at this zoom
                    if key not in maps[1]:
                        failures.append(f"{area['id']} z{zoom}: missing {key}")
                        continue
                    for target, geometry in zip(errors, maps):
                        target.extend(d * 512 * 2 ** zoom for d in distances(points, geometry[key]))
                passed = no_worse(*errors)
                if not passed:
                    failures.append(f"{area['id']} z{zoom}: bend error increased or no comparable vertices")
                stats = [f'{p95(e):.3f}' if e else 'no samples' for e in errors]
                print(f"| {area['id']} | {zoom} | {len(errors[0])} | {stats[0]} | {stats[1]} | {'PASS' if passed else 'FAIL'} |")
    print('\n' + ('PASS' if not failures else 'FAIL: ' + '; '.join(failures)))
    return bool(failures)


if __name__ == '__main__':
    sys.exit(compare(sys.argv[1], sys.argv[2], Path(sys.argv[3])))
