"""Build and validate one explicitly bounded region; no whole-UK output exists."""
import argparse
from datetime import datetime, timezone
import gzip
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import sys

import progressive_regions as regions
import publish_map_data as publisher
import regional_supplements
import search_offline
import verify_pmtiles
import zones

ROOT = Path(__file__).resolve().parents[1]


def command(*args, env=None, **kwargs):
    print('Running: ' + ' '.join(str(x) for x in args[:3]), flush=True)
    subprocess.run([str(x) for x in args], check=True, env=env, **kwargs)


def script(name, *args, env=None):
    command(sys.executable, ROOT / 'scripts' / (name + '.py'), *args, env=env)


def export(source, destination, geometry='point,linestring,polygon'):
    command('osmium', 'export', source, '-f', 'geojsonseq', '-o', destination,
            '--overwrite', '--geometry-types=' + geometry, '-a', 'type,id')


def filter_export(source, work, name, filters, geometry='point,linestring,polygon'):
    pbf = work / (name + '.osm.pbf')
    command('osmium', 'tags-filter', source, *filters, '-o', pbf, '--overwrite')
    target = work / (name + '.geojsonseq')
    export(pbf, target, geometry)
    return target


def geojson_features(path):
    # RFC 8142 record separators are part of each record prefix. Python's
    # str.splitlines() treats them as line breaks, creating bogus empty JSON.
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            text = line.strip().lstrip('\x1e').strip()
            if text:
                feature = json.loads(text)
                if not isinstance(feature, dict) or feature.get('type') != 'Feature':
                    raise ValueError('Unexpected GeoJSON sequence record')
                yield feature


def write_places_input(source, destination):
    # The POI merger accepts point/area geometry; road lines remain in the
    # full feature pack and fallback street search, never coerced to polygons.
    count = 0
    with Path(destination).open('w', encoding='utf-8') as stream:
        for feature in geojson_features(source):
            if (feature.get('geometry') or {}).get('type') in ('Point', 'Polygon', 'MultiPolygon'):
                stream.write(json.dumps(feature) + '\n')
                count += 1
    return count


def osm_search(geojson, output, bounds):
    """Fallback OSM search; retains mapped addresses/postcodes, makes no completeness claim."""
    lines = set()
    for feature in geojson_features(geojson):
        props = feature.get('properties', {})
        geometry = feature.get('geometry', {})
        def points(value):
            if isinstance(value, list) and len(value) >= 2 and all(type(v) in (float, int) for v in value[:2]):
                yield value[:2]
            elif isinstance(value, list):
                for row in value:
                    yield from points(row)
        coordinates = list(points(geometry.get('coordinates')))
        if not coordinates:
            continue
        lon = sum(p[0] for p in coordinates) / len(coordinates)
        lat = sum(p[1] for p in coordinates) / len(coordinates)
        if not bounds[0] <= lon <= bounds[2] or not bounds[1] <= lat <= bounds[3]:
            continue
        names = [str(props.get('name', ''))]
        street = props.get('addr:street')
        if street:
            names.append(str(props.get('addr:housenumber', '')) + ' ' + str(street))
        for name in names:
            key = search_offline.key_of(name)
            if key:
                values = [key, 'R' if props.get('highway') else 'O', name,
                          str(props.get('addr:city', props.get('addr:postcode', ''))), f'{lat:.5f}', f'{lon:.5f}']
                lines.add('\t'.join(search_offline.clean(v) for v in values))
        postcode = props.get('addr:postcode')
        if postcode:
            lines.add('\t'.join([search_offline.key_of(str(postcode), postcode=True), 'P', '', '', f'{lat:.5f}', f'{lon:.5f}']))
    header = '#drivemate-search-offline\t1\t' + datetime.now(timezone.utc).date().isoformat() + '\t© OpenStreetMap contributors, ODbL 1.0'
    Path(output).write_bytes(gzip.compress((header + '\n' + '\n'.join(sorted(lines)) + '\n').encode(), mtime=0))
    return len(lines)


def validate_local_map(path, region):
    report = verify_pmtiles.verify(path, layout='own')
    if report['errors']:
        raise ValueError('Regional archive failed verification')
    if region.get('sample'):
        from pmtiles.reader import Reader, MmapSource
        from mapbox_vector_tile import decode
        lon, lat = region['sample']
        x = int((lon + 180) / 360 * 2**14)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2**14)
        with open(path, 'rb') as stream:
            payload = Reader(MmapSource(stream)).get(14, x, y)
        if not payload:
            raise ValueError('Regional centre has no detailed map tile')
        tile = decode(gzip.decompress(payload))
        if not tile.get('roads', {}).get('features'):
            raise ValueError('Regional centre has no rendered road geometry')
        report['regional_road_sample'] = {'lon': lon, 'lat': lat, 'zoom': 14,
                                         'roads': len(tile['roads']['features'])}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--overture-release', default='unknown')
    args = parser.parse_args()
    region = json.loads(args.region.read_text())
    bounds = regions.checked_bounds(region['bounds'])
    out, work = args.out.resolve(), args.work.resolve()
    out.mkdir(parents=True)
    work.mkdir(parents=True)
    source = args.source.resolve()
    shutil.copyfile(source, out / 'regional-source.osm.pbf')
    source_status = [{'id': 'openstreetmap', 'status': 'imported', 'url': region['source_url'],
                      'sha256': publisher.digest(source), 'bytes': source.stat().st_size,
                      'attribution': '© OpenStreetMap contributors', 'licence': 'ODbL-1.0'}]
    # An inland-only build needs no global sea polygon download. Coastal regions
    # use the declared, cached OSM ocean source with the same bounded output.
    import yaml
    profile = yaml.safe_load((ROOT / 'schema/drivemate-map.yml').read_text())
    profile.pop('examples', None)
    if region.get('inland'):
        profile['sources'].pop('ocean')
        profile['layers'][0]['features'] = [f for f in profile['layers'][0]['features'] if f.get('source') != 'ocean']
    schema = work / 'region-map.yml'
    schema.write_text(yaml.safe_dump(profile, sort_keys=False))
    archive = out / 'drivemate-v1.pmtiles'
    command('java', '-Xmx6g', '-jar', ROOT / 'work/planetiler.jar', 'generate-custom', '--schema=' + str(schema),
            '--osm_path=' + str(source), '--ocean_path=' + str(ROOT / 'work/sources/water-polygons-split-3857.zip'),
            '--output=' + str(archive), '--bounds=' + ','.join(map(str, bounds)), '--download', '--download_osm_tile_weights=false', '--force')
    map_report = validate_local_map(archive, region)
    (out / 'map-validation.json').write_text(json.dumps(map_report, indent=2) + '\n')
    # Preserve every exported tag/feature and relation, not only visible map layers.
    everything = work / 'all.geojsonseq'
    export(source, everything)
    script('osm_feature_packs', '--source', everything, '--output', out / 'features')
    relation_opl = work / 'relations.opl'
    command('osmium', 'cat', source, '--object-type', 'relation', '-f', 'opl,add_metadata=false', '-o', relation_opl, '--overwrite')
    script('osm_relation_packs', '--input', relation_opl, '--output', out / 'relations')
    roads = filter_export(source, work, 'roads', ['w/highway'], 'linestring')
    script('lanes', roads, out / 'lanes', '--details-out', out / 'road-details')
    maxspeed = filter_export(source, work, 'maxspeed', ['w/maxspeed'], 'linestring')
    motorway = filter_export(source, work, 'motorway', ['w/highway=motorway'], 'linestring')
    limits_env = {**os.environ, 'LIMITS_MIN_WAYS': '1'}
    script('limits', maxspeed, out / 'limits', motorway, out / 'limit-checks.md', env=limits_env)
    script('roadinfo', everything, out / 'roadinfo', '--min-elements', '1')
    camera_pbf = work / 'cameras.osm.pbf'
    command('osmium', 'tags-filter', source, 'n/highway=speed_camera', 'n/railway=level_crossing',
            'r/type=enforcement', '-o', camera_pbf, '--overwrite')
    camera_opl = work / 'cameras.opl'
    command('osmium', 'cat', camera_pbf, '-f', 'opl,add_metadata=false', '-o', camera_opl, '--overwrite')
    script('cameras', camera_opl, out / 'cameras-uk.json')
    # Zero mapped zones in a region is valid; retain the existing parser and
    # explicit regional scope rather than applying the old whole-UK count gate.
    zone_source = filter_export(source, work, 'zones', ['r/boundary=low_emission_zone', 'w/boundary=low_emission_zone'], 'polygon')
    (out / 'charge-zones-uk.json').write_text(json.dumps(zones.build(str(zone_source), [], None)))
    # Only this region's Overture places are downloaded, never the UK dataset.
    place_features = work / 'place-features.geojsonseq'
    write_places_input(everything, place_features)
    places_args = [str(out / 'places'), '--osm', str(place_features), '--osm-addresses', str(place_features)]
    overture = work / 'places.parquet'
    overture_command = ['overturemaps', 'download', '--bbox=' + ','.join(map(str, bounds)),
                        '-f', 'geoparquet', '--type=place', '-o', str(overture)]
    if args.overture_release != 'unknown':
        overture_command += ['--release', args.overture_release]
    try:
        result = subprocess.run(overture_command, timeout=600)
        overture_ok = result.returncode == 0
    except subprocess.TimeoutExpired:
        overture_ok = False
    if overture_ok and overture.is_file() and overture.stat().st_size:
        places_args += ['--overture', str(overture), '--overture-release', args.overture_release]
        shutil.copyfile(overture, out / 'overture-places.parquet')
        source_status.append({'id': 'overture-places', 'status': 'imported', 'bounds': bounds,
                              'release': args.overture_release, 'sha256': publisher.digest(overture)})
    else:
        source_status.append({'id': 'overture-places', 'status': 'unavailable'})
        if overture.exists():
            overture.unlink()
    # HTTP ranges fetch only the necessary OS CSV grid sheets from the official
    # ZIP; there is deliberately no fallback to downloading the GB archive.
    try:
        os_names = regional_supplements.import_os_open_names(bounds, work / 'osnames')
    except ValueError:
        os_names = {'status': 'unavailable', 'reason': 'Region requires smaller OS grid-sheet batches'}
    source_status.append({'id': 'os-open-names', **os_names})
    (out / 'source-import-status.json').write_text(json.dumps(source_status, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'supplementary_sources': [{'id': row['id'], 'status': row['status'], 'reason': row.get('reason'), 'detail': row.get('detail')} for row in source_status]}), flush=True)
    if os_names['status'] == 'imported':
        # Preserve all selected official CSV fields for future map improvements.
        with tarfile.open(out / 'os-open-names-regional.tar.gz', 'w:gz') as archive:
            for csv_path in sorted(Path(os_names['directory']).glob('*.csv')):
                archive.add(csv_path, arcname=csv_path.name, recursive=False)
        places_args += ['--osnames', str(os_names['directory'])]
        search_stats = search_offline.build(str(os_names['directory']), str(out / 'search-offline-uk.tsv.gz'))
    else:
        search_stats = {'records': osm_search(everything, out / 'search-offline-uk.tsv.gz', bounds),
                        'source': 'OSM fallback; postcode completeness not claimed'}
    script('places', *places_args)
    script('detail_layer', everything, out / 'road-signs.geojsonseq')
    # Per-region routing graph. It is separate from app-specific DMN packs.
    script('restriction_rewrite', '--input', source, '--output', work / 'routing.osm.pbf', '--report', out / 'restriction-rewrite.json')
    import valhalla
    valhalla_root = Path(valhalla.__file__).parent
    config = work / 'valhalla.json'
    with config.open('w') as stream:
        command(sys.executable, valhalla_root / 'valhalla_build_config.py',
                '--mjolnir-tile-dir', work / 'tiles', '--mjolnir-tile-extract', out / 'routing-region.tar',
                '--mjolnir-admin', work / 'admin.sqlite', '--mjolnir-concurrency', '2', stdout=stream)
    command('valhalla_build_admins', '-c', config, work / 'routing.osm.pbf')
    command('valhalla_build_tiles', '-c', config, work / 'routing.osm.pbf')
    command(sys.executable, ROOT / 'work/valhalla_build_extract', '-c', config)
    graph = out / 'routing-region.tar'
    if not graph.is_file() or graph.stat().st_size < 1024:
        raise ValueError('Regional routing graph missing')
    # Pick mapped road coordinates in the region; verify that the generated
    # graph actually answers a route before allowing this bundle to publish.
    route_samples = []
    from valhalla import Actor, get_config
    empty = work / 'empty'
    empty.mkdir()
    actor = Actor(get_config(tile_extract=str(graph), tile_dir=str(empty), verbose=False))
    for feature in geojson_features(roads):
        geometry = feature.get('geometry', {})
        if geometry.get('type') != 'LineString':
            continue
        coords = geometry['coordinates']
        if len(coords) < 2:
            continue
        a, b = coords[0], coords[-1]
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 0.002:
            continue
        if not all(bounds[0] <= p[0] <= bounds[2] and bounds[1] <= p[1] <= bounds[3] for p in (a, b)):
            continue
        try:
            value = json.loads(actor.route(json.dumps({'locations': [{'lon': p[0], 'lat': p[1]} for p in (a, b)], 'costing': 'auto'})))
            length = value['trip']['summary']['length']
            if length > 0:
                route_samples.append({'start': a, 'end': b, 'length': length})
        except Exception:
            pass
        if len(route_samples) == 3:
            break
    if not route_samples:
        raise ValueError('Regional graph answered no sampled road routes')
    (out / 'routing-validation.json').write_text(json.dumps({'engine': 'Valhalla 3.6.3', 'routes': route_samples,
        'scope': 'Regional graph sanity checks, not complete navigation certification', 'errors': []}, indent=2) + '\n')
    for path in (ROOT / 'licenses').iterdir():
        if path.is_file():
            shutil.copyfile(path, out / path.name)
    source_status += [{'id': 'national-highways', 'status': 'separate_live_source',
                       'note': 'Official snapshots and app live lookup have separate expiry; never permanent road rules'},
                      {'id': 'traffic-and-prices', 'status': 'provider_access_required',
                       'note': 'No comprehensive live traffic/fuel/parking/toll tariffs fabricated from OSM'}]
    inventory = {'schema': 1, 'scope': 'regional', 'region': region, 'sources': source_status,
                 'search': search_stats, 'retrieved_utc': datetime.now(timezone.utc).isoformat(),
                 'limitations': ['Region bounds are rectangular areas, not official council boundaries.',
                     'Mapped tags and geometry do not establish that every road feature is known.',
                     'Routing graph is separate from the app-specific DMN runtime.',
                     'Unsupported supplementary feeds remain explicitly unavailable.']}
    (out / 'source-inventory.json').write_text(json.dumps(inventory, sort_keys=True, indent=2) + '\n')
    assets = {p.name: {'bytes': p.stat().st_size, 'sha256': publisher.digest(p)} for p in sorted(out.rglob('*')) if p.is_file()}
    if len(assets) != len([p for p in out.rglob('*') if p.is_file()]):
        raise ValueError('Duplicate regional asset name')
    quality = {'schema': 1, 'region': region['id'], 'bounds': bounds, 'errors': [],
               'map': map_report, 'route_samples': len(route_samples), 'assets': assets,
               'scope': 'Region-scoped archive, data and graph validation; on-device render not yet verified'}
    (out / 'region-quality.json').write_text(json.dumps(quality, sort_keys=True, indent=2) + '\n')


if __name__ == '__main__':
    main()
