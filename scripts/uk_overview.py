"""One low-detail map of the whole UK, Isle of Man and Channel Islands for zoomed-out views.

The regional archives hold full detail for their own area only, so a view across
many regions had to stitch up to nine of them and could still show blank patches.
This overview carries just what a zoomed-out driving map draws (zoom 0 to
MAXZOOM: sea, lakes, woods, main roads, ferries, towns) for the whole area in one
file. The app draws it when zoomed out and the regional detail when zoomed in.

It is published on its own immutable tag with a small pointer (overview.json on
the map-data-overview release), never through the regional catalog, so it cannot
disturb the regional update chain.
"""
import argparse
from datetime import datetime, timezone
import gzip
import json
import math
import os
from pathlib import Path
import subprocess

import publish_map_data as publisher

ROOT = Path(__file__).resolve().parents[1]
POINTER_TAG = 'map-data-overview'
POINTER = 'overview.json'
MAP_NAME = 'drivemate-overview.pmtiles'
MAXZOOM = 10
BOUNDS = [-8.75, 49.1, 1.9, 60.9]
SOURCES = [
    'https://download.geofabrik.de/europe/united-kingdom-latest.osm.pbf',
    'https://download.geofabrik.de/europe/isle-of-man-latest.osm.pbf',
    'https://download.geofabrik.de/europe/guernsey-jersey-latest.osm.pbf',
]
# Every OSM feature the schema draws at or below MAXZOOM; nothing else is kept.
FILTERS = [
    'w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link',
    'w/route=ferry',
    'nwr/place=city,town,village,suburb',
    'wr/natural=water,wood,grassland,heath,scrub,fell',
    'wr/waterway=riverbank,dock',
    'wr/landuse=reservoir,basin,forest,grass,meadow,village_green,recreation_ground,allotments',
    'wr/leisure=nature_reserve,park,recreation_ground,golf_course,common,village_green',
    'wr/boundary=national_park',
]
# Each must show roads at zoom 8 (one per nation and island group).
SAMPLES = {'Manchester': (53.48, -2.24), 'Cardiff': (51.48, -3.18), 'Edinburgh': (55.95, -3.19),
           'Belfast': (54.60, -5.93), 'Douglas': (54.15, -4.48), 'St Helier': (49.19, -2.11),
           'London': (51.51, -0.13), 'Inverness': (57.48, -4.22)}


def run(*args):
    print('Running: ' + ' '.join(str(a) for a in args[:3]), flush=True)
    subprocess.run([str(a) for a in args], check=True)


def tile_of(lat, lon, z):
    x = int((lon + 180) / 360 * 2**z)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2**z)
    return x, y


def check_samples(archive, zoom=8):
    """Roads at zoom [zoom] near every sample town; returns their feature counts."""
    from mapbox_vector_tile import decode
    from pmtiles.reader import Reader, MmapSource
    found, missing = {}, []
    with open(archive, 'rb') as stream:
        reader = Reader(MmapSource(stream))
        for name, (lat, lon) in SAMPLES.items():
            payload = reader.get(zoom, *tile_of(lat, lon, zoom))
            roads = len(decode(gzip.decompress(payload)).get('roads', {}).get('features', [])) if payload else 0
            found[name] = roads
            if not roads:
                missing.append(name)
    if missing:
        raise ValueError('Overview map has no roads near ' + ', '.join(missing))
    return found


def build(sources, work, out):
    """Filter and merge [sources], then draw them with the DriveMate schema up to MAXZOOM."""
    work, out = Path(work), Path(out)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    filtered = []
    for i, source in enumerate(sources):
        target = work / f'overview-{i}.osm.pbf'
        run('osmium', 'tags-filter', source, *FILTERS, '-o', target, '--overwrite')
        filtered.append(target)
    merged = work / 'overview.osm.pbf'
    run('osmium', 'merge', *filtered, '-o', merged, '--overwrite')
    archive = out / MAP_NAME
    run('java', '-Xmx6g', '-jar', ROOT / 'work/planetiler.jar', 'generate-custom',
        '--schema=' + str(ROOT / 'schema/drivemate-map.yml'), '--osm_path=' + str(merged),
        '--ocean_path=' + str(ROOT / 'work/sources/water-polygons-split-3857.zip'),
        '--output=' + str(archive), '--bounds=' + ','.join(map(str, BOUNDS)),
        f'--maxzoom={MAXZOOM}', f'--render_maxzoom={MAXZOOM}',
        '--download_osm_tile_weights=false', '--force')
    import verify_pmtiles
    report = verify_pmtiles.verify(archive, layout='own')
    if report['errors']:
        raise ValueError('Overview archive failed verification')
    report['samples_z8_roads'] = check_samples(archive)
    (out / 'overview-validation.json').write_text(json.dumps(report, indent=2) + '\n')
    return archive, report


def pointer_document(tag, archive, sources):
    return {'schema': 1, 'maxzoom': MAXZOOM, 'bounds': BOUNDS, 'built_utc': datetime.now(timezone.utc).isoformat(),
            'sources': sources, 'attribution': '© OpenStreetMap contributors (ODbL)',
            'map': {**publisher.metadata(archive, tag), 'name': MAP_NAME, 'layout': 'drivemate-v1'}}


def validate_pointer(document):
    if (not isinstance(document, dict) or document.get('schema') != 1 or document.get('maxzoom') != MAXZOOM
            or document.get('bounds') != BOUNDS):
        raise ValueError('Unsupported overview pointer')
    asset = document.get('map') or {}
    publisher.checked_tag(asset.get('tag'))
    if (asset.get('name') != MAP_NAME or asset.get('layout') != 'drivemate-v1'
            or not str(asset.get('tag')).startswith(POINTER_TAG + '-r')
            or type(asset.get('bytes')) is not int or not 0 < asset['bytes'] <= publisher.MAX_ASSET_BYTES
            or len(str(asset.get('sha256', ''))) != 64):
        raise ValueError('Invalid overview map asset')
    return document


def publish(github, archive, tag, directory, sources):
    """Upload the map to its own immutable tag, verify it, then move the pointer."""
    publisher.checked_tag(tag)
    publisher.upload_immutable(github, tag, {MAP_NAME: archive})
    publisher.verify_inventory(github.inventory(tag), {MAP_NAME: publisher.metadata(archive, tag)}, tag, exact=True)
    document = validate_pointer(pointer_document(tag, archive, sources))
    target = Path(directory) / POINTER
    target.write_text(json.dumps(document, sort_keys=True, indent=2) + '\n')
    github.ensure_release(POINTER_TAG)
    github.run('release', 'upload', POINTER_TAG, str(target), '--repo', github.repo, '--clobber')
    publisher.verify_inventory(github.inventory(POINTER_TAG), {POINTER: publisher.metadata(target, POINTER_TAG)}, POINTER_TAG)
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--sources', type=Path, required=True, help='Folder holding the downloaded extracts')
    parser.add_argument('--work', type=Path, default=Path('work/overview'))
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    local = [args.sources / url.rsplit('/', 1)[1] for url in SOURCES]
    archive, report = build(local, args.work / 'build', args.work / 'out')
    print(json.dumps({'overview_bytes': report['bytes'], 'samples_z8_roads': report['samples_z8_roads']}), flush=True)
    if args.validate_only:
        return
    tag = f"{POINTER_TAG}-r{os.environ['GITHUB_RUN_ID']}-a{os.environ['GITHUB_RUN_ATTEMPT']}"
    publish(publisher.GitHub(args.repo), archive, tag, args.work, SOURCES)
    print('Published UK overview map: ' + tag)


if __name__ == '__main__':
    main()
