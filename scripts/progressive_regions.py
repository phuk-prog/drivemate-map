"""Stockport-first independent region planning, change detection and publication.

Never builds a whole-UK archive. Immutable regional generations are selected by
one small catalog, promoted only after complete upload and integrity validation.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen

import daily_schedule
import daily_source_probe
import osm_changes
import publish_map_data as publisher

ROOT = Path(__file__).resolve().parents[1]
CATALOG_TAG = 'map-regions'
CATALOG_FILE = 'catalog.json'
GM_SOURCE = 'https://download.geofabrik.de/europe/united-kingdom/england/greater-manchester-latest.osm.pbf'
INITIAL = [
    {'id': 'stockport', 'name': 'Stockport area', 'bounds': [-2.29, 53.30, -1.98, 53.47],
     'source_url': GM_SOURCE, 'sample': [-2.1575, 53.4052], 'inland': True},
    {'id': 'manchester', 'name': 'Manchester area', 'bounds': [-2.35, 53.36, -2.12, 53.56],
     'source_url': GM_SOURCE, 'sample': [-2.2309, 53.4774], 'inland': True},
]
# Crown dependencies are outside the four-nation UK map scope. Retire
# any historical catalogue references without deleting immutable releases.
OUT_OF_SCOPE = frozenset(('isle-of-man', 'guernsey-jersey'))
# Scripts that do not affect a region's files: changing them must not rebuild every region.
NOT_BUILD_INPUTS = ('scripts/uk_overview.py', 'scripts/map_watchdog.py', 'scripts/daily_schedule.py',
                    'scripts/publish_map_data.py', 'scripts/uk_join.py', 'scripts/uk_map.py',
                    'scripts/dmn_due.py', 'scripts/dmn_tiles.py', 'scripts/dmn_pack_check.py')
ID = re.compile(r'[a-z0-9][a-z0-9-]{0,70}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')


def checked_bounds(bounds):
    if (not isinstance(bounds, list) or len(bounds) != 4
            or any(type(n) not in (int, float) or not math.isfinite(n) for n in bounds)
            or not -180 <= bounds[0] < bounds[2] <= 180
            or not -85 <= bounds[1] < bounds[3] <= 85):
        raise ValueError('Invalid regional bounds')
    return bounds


def read_json_url(url, maximum=20_000_000, *, attempts=4, opener=None, sleeper=None):
    """Small JSON metadata (the Geofabrik index); transient failures are retried with waits."""
    if not url.startswith('https://'):
        raise ValueError('HTTPS source required')
    opener = opener or urlopen
    sleeper = sleeper or time.sleep
    for attempt in range(attempts):
        try:
            with opener(url, timeout=60) as response:
                data = response.read(maximum + 1)
            break
        except (OSError, TimeoutError) as exc:
            if isinstance(exc, HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == attempts - 1:
                raise
            sleeper(min(5 * 2 ** attempt, 40))
    if len(data) > maximum:
        raise ValueError('Source metadata too large')
    return json.loads(data)


def flatten_coordinates(geometry):
    if not isinstance(geometry, dict) or geometry.get('type') not in ('Polygon', 'MultiPolygon'):
        raise ValueError('Source extract lacks polygon geometry')
    def walk(values):
        if isinstance(values, list) and len(values) >= 2 and all(type(v) in (int, float) for v in values[:2]):
            yield values[:2]
        elif isinstance(values, list):
            for value in values:
                yield from walk(value)
    points = list(walk(geometry.get('coordinates')))
    if not points:
        raise ValueError('Empty extract geometry')
    return checked_bounds([min(p[0] for p in points), min(p[1] for p in points),
                           max(p[0] for p in points), max(p[1] for p in points)])


def expansion_plan(index):
    """Choose smallest available UK extracts, sorted outwards from Stockport.

    The upstream UK download is never selected. NI uses the smaller Ireland
    extract clipped to a declared NI-area rectangle (which includes border land).
    """
    features = index.get('features') if isinstance(index, dict) else None
    if not isinstance(features, list):
        raise ValueError('Invalid Geofabrik index')
    records = {f.get('properties', {}).get('id'): f for f in features if isinstance(f, dict)}
    def within_uk(key):
        seen = set()
        while key in records and key not in seen:
            if key == 'united-kingdom':
                return True
            seen.add(key)
            key = records[key]['properties'].get('parent')
        return False
    uk = {k: f for k, f in records.items() if isinstance(k, str) and within_uk(k)
          and k not in ('united-kingdom', 'bermuda', 'falklands')}
    parents = {f['properties'].get('parent') for f in uk.values()}
    choices = []
    for key, feature in uk.items():
        if key in parents:
            continue
        if not ID.fullmatch(key):
            raise ValueError('Unsafe source region identifier')
        props = feature['properties']
        url = daily_source_probe.source_url(props.get('urls', {}).get('pbf'))
        if '/united-kingdom/' not in url:
            continue
        b = flatten_coordinates(feature.get('geometry'))
        choices.append({'id': key, 'name': props.get('name', key), 'bounds': b,
                        'source_url': url, 'inland': key == 'greater-manchester'})
    if not choices:
        raise ValueError('No UK regional extracts discovered; refusing UK fallback')
    choices.append({'id': 'northern-ireland-area', 'name': 'Northern Ireland and border area',
                    'bounds': [-8.2, 54.0, -5.3, 55.5],
                    'source_url': 'https://download.geofabrik.de/europe/ireland-and-northern-ireland-latest.osm.pbf',
                    'inland': False})
    def distance(region):
        w, s, e, n = region['bounds']
        return (((w + e) / 2 + 2.15) * 0.6)**2 + ((s + n) / 2 - 53.4)**2
    # Establish coverage in all four UK nations early; then expand through the
    # smallest source regions near Manchester. These are still only source
    # footprints, not claims of verified road-by-road national coverage.
    nation_priority = {'greater-manchester': 0, 'wales': 1, 'scotland': 2,
                       'northern-ireland-area': 3}
    return copy.deepcopy(INITIAL) + sorted(
        choices, key=lambda r: (nation_priority.get(r['id'], 4), distance(r), r['id']))


def validate_catalog(document):
    if (not isinstance(document, dict) or document.get('schema') != 1
            or document.get('coverage') != 'partial' or not isinstance(document.get('regions'), dict)
            or len(document['regions']) > 256):
        raise ValueError('Unsupported regional catalog')
    for key, region in document['regions'].items():
        if not ID.fullmatch(key) or region.get('id') != key:
            raise ValueError('Unsafe catalog region')
        checked_bounds(region.get('bounds'))
        for field in ('content_sha256', 'pipeline_sha256'):
            if not SHA.fullmatch(str(region.get(field, ''))):
                raise ValueError('Missing regional content identity')
        for field in ('manifest', 'map'):
            asset = region.get(field)
            if (not isinstance(asset, dict) or type(asset.get('bytes')) is not int
                    or not 0 < asset['bytes'] <= 1_950_000_000
                    or not SHA.fullmatch(str(asset.get('sha256', '')))
                    or not str(asset.get('tag', '')).startswith('map-data-region-' + key + '-')):
                raise ValueError('Invalid regional asset')
            publisher.checked_tag(asset['tag'])
        if region['map'].get('name') != 'drivemate-v1.pmtiles' or region['map'].get('layout') != 'drivemate-v1':
            raise ValueError('Unsupported regional map layout')
    return document


def load_catalog(github, directory):
    try:
        assets = github.inventory(CATALOG_TAG)
    except publisher.GitHubError as error:
        if 'HTTP 404' in str(error):
            return {'schema': 1, 'coverage': 'partial', 'regions': {}}
        raise
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = github.download(CATALOG_TAG, CATALOG_FILE, directory)
    if path.stat().st_size > 2_000_000:
        raise ValueError('Regional catalog too large')
    publisher.verify_inventory(assets, {CATALOG_FILE: publisher.metadata(path, CATALOG_TAG)}, CATALOG_TAG)
    return validate_catalog(json.loads(path.read_text()))


def read_source_signal(url, *, attempts=4, opener=None, sleeper=None):
    """Bounded retries for transient Geofabrik failures; never invent a checksum."""
    daily_source_probe.source_url(url)
    if type(attempts) is not int or not 1 <= attempts <= 6:
        raise ValueError('Invalid checksum retry count')
    opener = opener or urlopen
    sleeper = sleeper or time.sleep
    for attempt in range(attempts):
        try:
            with opener(url + '.md5', timeout=40) as response:
                value = response.read(4097).decode('ascii')
            return daily_source_probe.parse_signal(value, url)['value']
        except (OSError, TimeoutError) as exc:
            if isinstance(exc, HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == attempts - 1:
                raise RuntimeError('Geofabrik checksum unavailable after retries: ' + url) from exc
            sleeper(min(2 ** attempt, 8))


def select_sources(plan, catalog, event_name):
    """Prioritise the next missing extract; daily schedules still check old maps."""
    published = catalog['regions']
    next_unbuilt = next((r for r in plan if r['id'] not in published), None)
    if next_unbuilt and event_name != 'schedule':
        return {next_unbuilt['source_url']}, next_unbuilt
    active = {r['source_url'] for r in plan if r['id'] in published}
    if next_unbuilt:
        active.add(next_unbuilt['source_url'])
    return active, None


def choose_next(plan, catalog, signals, pipeline_sha, supplement_version, changes_slot=None, skip=()):
    """Update changed existing regions first; otherwise expand by exactly one region.

    A region is changed when its Geofabrik extract, the pipeline, the supplementary
    sources or the 01:00 / 14:00 OSM change slot moved on. Regions in [skip] (failed
    earlier in this update) and regions whose change signal could not be read are
    left for the next update rather than stopping every other region.
    """
    regions = validate_catalog(catalog)['regions']
    for region in plan:
        if region['id'] in skip or region['source_url'] not in signals:
            continue
        old = regions.get(region['id'])
        if old is not None and (old.get('observed_source_md5') != signals[region['source_url']]
                or old['pipeline_sha256'] != pipeline_sha
                or old.get('supplement_version') != supplement_version
                or (changes_slot is not None and old.get('osm_changes_slot') != changes_slot)):
            return region
    return next((r for r in plan if r['id'] not in regions and r['id'] not in skip
                 and r['source_url'] in signals), None)


def read_signals(urls, reader=None):
    """Change signals for [urls]; one unreachable source only holds back its own regions."""
    reader = reader or read_source_signal
    signals = {}
    for url in sorted(urls):
        try:
            signals[url] = reader(url)
        except Exception as exc:
            print(f'::warning::Change signal unavailable, regions from it wait for the next update: {url}: {exc}', flush=True)
    return signals


def publish_catalog(github, previous, updated, directory):
    validate_catalog(updated)
    # Single workflow writer plus a remote identity check prevents overwriting
    # a catalog changed outside the serialized pipeline.
    live = load_catalog(github, Path(directory) / 'catalog-current')
    if live != previous:
        raise ValueError('Regional catalog changed during build; nothing promoted')
    target = Path(directory) / CATALOG_FILE
    target.write_text(json.dumps(updated, sort_keys=True, indent=2) + '\n')
    github.ensure_release(CATALOG_TAG)
    # The only mutable asset here is the catalog; dated map assets never change.
    github.run('release', 'upload', CATALOG_TAG, str(target), '--repo', github.repo, '--clobber')
    publisher.verify_inventory(github.inventory(CATALOG_TAG),
                               {CATALOG_FILE: publisher.metadata(target, CATALOG_TAG)}, CATALOG_TAG)


def publish_region(github, region, out, tag, checkpoint, previous, directory, promote=True):
    out = Path(out)
    publisher.checked_tag(tag)
    files = {}
    for path in sorted(out.rglob('*')):
        if path.is_symlink():
            raise ValueError('Regional symlink prohibited')
        if path.is_file():
            publisher.checked_name(path.name)
            if path.name in files or not 0 < path.stat().st_size <= 1_950_000_000:
                raise ValueError('Duplicate, empty or oversized regional asset')
            files[path.name] = path
    required = {'drivemate-v1.pmtiles', 'region-quality.json', 'regional-source.osm.pbf',
                'source-inventory.json', 'NOTICE.md'}
    if not required <= files.keys():
        raise ValueError('Incomplete regional generation')
    quality = json.loads(files['region-quality.json'].read_text())
    if quality.get('errors') != [] or quality.get('region') != region['id']:
        raise ValueError('Regional validation did not pass')
    if quality.get('assets') != {name: {'bytes': p.stat().st_size, 'sha256': publisher.digest(p)}
                                 for name, p in files.items() if name != 'region-quality.json'}:
        raise ValueError('Regional assets changed after validation')
    # A valid build-quality snapshot alone cannot certify the rights metadata.
    # Check the *complete logical regional bundle* before any remote writes;
    # immutable shard reuse later may place these files on older release tags.
    if any(publisher.PLACE_TILE.fullmatch(name) for name in files):
        if publisher.places_provenance.FILENAME not in files:
            raise ValueError('Regional places provenance sidecar missing')
        if not set(publisher.LICENSE_ASSETS) <= files.keys():
            raise ValueError('Regional places licence/notice assets missing')
    if publisher.places_provenance.FILENAME in files:
        publisher.places_provenance.validate(
            json.loads(files[publisher.places_provenance.FILENAME].read_text()))
    reused = {}
    prior = previous['regions'].get(region['id'])
    if prior:
        old_dir = Path(directory) / 'reuse'
        old_dir.mkdir()
        old_info = prior['manifest']
        old_path = github.download(old_info['tag'], 'manifest.json', old_dir)
        if old_path.stat().st_size != old_info['bytes'] or publisher.digest(old_path) != old_info['sha256']:
            raise ValueError('Old regional manifest integrity failed')
        old_files = json.loads(old_path.read_text()).get('files', {})
        inventories = {}
        for name, path in files.items():
            old = old_files.get(name)
            if not isinstance(old, dict):
                continue
            old_tag = old.get('tag', '')
            if not old_tag.startswith('map-data-region-' + region['id'] + '-'):
                raise ValueError('Cross-region immutable asset reuse prohibited')
            wanted = publisher.metadata(path, old_tag)
            if wanted == old:
                if old_tag not in inventories:
                    inventories[old_tag] = github.inventory(old_tag)
                publisher.verify_inventory(inventories[old_tag], {name: wanted}, old_tag)
                reused[name] = wanted
    # Each GitHub Release supports at most 1,000 assets. Large UK nations
    # routinely exceed that, so allocate immutable regional shard tags before
    # writing the manifest. Only the first shard holds manifest.json.
    pending = {name: path for name, path in files.items() if name not in reused}
    shard_limit = min(publisher.MAIN_FILES, publisher.ASSET_LIMIT - 1)
    if shard_limit < 2:
        raise ValueError('GitHub asset limit too small for regional manifest')
    names = sorted(pending)
    shard_groups = [names[i:i + shard_limit] for i in range(0, len(names), shard_limit)]
    if not shard_groups:
        shard_groups = [[]]
    shards = []
    expected_files = dict(reused)
    for idx, group in enumerate(shard_groups):
        shard_tag = tag if idx == 0 else f'{tag}-part{idx + 1:03d}'
        publisher.checked_tag(shard_tag)
        shard_files = {name: pending[name] for name in group}
        shards.append((shard_tag, shard_files))
        for name in group:
            expected_files[name] = publisher.metadata(pending[name], shard_tag)
    manifest = {'schema': 1, 'coverage': 'partial', 'region': region, 'files': expected_files}
    manifest_path = Path(directory) / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + '\n')
    if manifest_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Regional manifest exceeds app download limit')
    shards[0][1]['manifest.json'] = manifest_path
    # An incomplete shard, checksum mismatch or upload failure never advances
    # the live catalog. Reuse is allowed only for the SAME named region.
    for shard_tag, shard_files in shards:
        publisher.upload_immutable(github, shard_tag, shard_files)
        expected = {name: publisher.metadata(path, shard_tag) for name, path in shard_files.items()}
        publisher.verify_inventory(github.inventory(shard_tag), expected, shard_tag, exact=True)
    for old_tag in {entry['tag'] for entry in reused.values()}:
        publisher.verify_inventory(github.inventory(old_tag), {n: e for n, e in reused.items() if e['tag'] == old_tag}, old_tag)
    entry = {**copy.deepcopy(region), **checkpoint,
             'published_utc': datetime.now(timezone.utc).isoformat(),
             'manifest': publisher.metadata(manifest_path, tag),
             'map': {**expected_files['drivemate-v1.pmtiles'], 'name': 'drivemate-v1.pmtiles', 'layout': 'drivemate-v1'}}
    if not promote:
        # Parallel builds: the files are uploaded and verified; one promote job adds the entry.
        return entry
    updated = copy.deepcopy(previous)
    updated['regions'][region['id']] = entry
    publish_catalog(github, previous, updated, directory)
    return updated


def run(*args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)


def context(github, work):
    """The inputs every step of one update agrees on: plan, catalogue, identities, slot."""
    index = read_json_url('https://download.geofabrik.de/index-v1.json')
    plan = expansion_plan(index)
    catalog = load_catalog(github, Path(work) / 'previous')
    fingerprint = daily_source_probe.pipeline_fingerprint(ROOT, includes=('.github/workflows/regional-maps.yml',), excludes=NOT_BUILD_INPUTS)['pipeline_sha256']
    release = subprocess.run(['overturemaps', 'releases', 'latest'], capture_output=True, text=True)
    overture_version = release.stdout.strip().splitlines()[-1] if release.returncode == 0 and release.stdout.strip() else 'unknown'
    if not re.fullmatch(r'20\d\d-\d\d-\d\d\.\d+', overture_version):
        overture_version = 'unknown'
    # Supplementary source releases are independent of OSM changes. Include the
    # official OS distribution digest/name in change detection.
    import regional_supplements
    try:
        listing = read_json_url(regional_supplements.API, maximum=1_000_000)
        os_version = [{k: item.get(k) for k in ('fileName', 'md5', 'size')} for item in listing]
    except Exception:
        os_version = 'unavailable'
    version = json.dumps({'overture': overture_version, 'os-open-names': os_version}, sort_keys=True)
    slot = daily_schedule.latest_slot(datetime.now(timezone.utc))
    return {'plan': plan, 'catalog': catalog, 'fingerprint': fingerprint, 'overture_version': overture_version,
            'version': version, 'slot': slot.isoformat(), 'changes_slot': slot.strftime('%Y-%m-%dT%H:%MZ')}


def due_regions(plan, catalog, signals, fingerprint, version, changes_slot, limit=256):
    """Every region this update must build: changed published regions first, then unbuilt ones."""
    due, skip = [], set()
    while len(due) < limit:
        region = choose_next(plan, catalog, signals, fingerprint, version, changes_slot, skip)
        if region is None:
            break
        due.append(region)
        skip.add(region['id'])
    return due


def cmd_plan(args, github):
    ctx = context(github, args.work)
    signals = read_signals({r['source_url'] for r in ctx['plan']})
    due = due_regions(ctx['plan'], ctx['catalog'], signals, ctx['fingerprint'], ctx['version'], ctx['changes_slot'])
    print(f"Published map regions: {len(ctx['catalog']['regions'])} of {len(ctx['plan'])} planned; "
          f"{len(due)} to build for {ctx['changes_slot']}", flush=True)
    if due:
        # One shared change file per slot, made here once so the parallel builds only download it.
        try:
            osm_changes.ensure(github, datetime.fromisoformat(ctx['slot']), args.work / 'changes')
        except Exception as exc:
            print(f'::warning::OpenStreetMap hourly changes unavailable for this update: {exc}', flush=True)
    document = {key: ctx[key] for key in ('fingerprint', 'overture_version', 'version', 'slot', 'changes_slot')}
    document.update({'signals': signals, 'regions': {r['id']: r for r in due}})
    Path(args.out).write_text(json.dumps(document, sort_keys=True))
    print('matrix=' + json.dumps([r['id'] for r in due]))
    return document


def cmd_build(args, github):
    """Build one planned region and upload its files; writes its catalogue entry, promotes nothing."""
    planned = json.loads(Path(args.plan).read_text())
    region = planned['regions'][args.region]
    signals, fingerprint, version = planned['signals'], planned['fingerprint'], planned['version']
    changes_slot, slot = planned['changes_slot'], datetime.fromisoformat(planned['slot'])
    catalog = load_catalog(github, args.work / 'previous')
    root = args.work / region['id']
    root.mkdir(parents=True)
    (root / 'region.json').write_text(json.dumps(region))
    source = args.work / 'source.osm.pbf'
    run('curl', '--fail', '--silent', '--show-error', '--location', '--proto', '=https',
        '--retry', '5', '--retry-all-errors', '--retry-delay', '4',
        '--connect-timeout', '30', '--max-time', '1800',
        '--output', source, region['source_url'])
    # Verify observed checksum before trusting the source or clipping any region.
    digest = hashlib.md5(usedforsecurity=False)
    with source.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != signals[region['source_url']]:
        raise ValueError('Regional upstream source changed during download; no publication')
    # Bring the extract up to this update slot with the shared hourly changes. If the
    # replication server is down the extract is used as published and noted.
    try:
        changes = osm_changes.ensure(github, slot, args.work / 'changes')
        updated = args.work / 'updated.osm.pbf'
        osm_changes.apply(source, changes, updated)
        source.unlink()
        source = updated
        applied_slot = changes_slot
        print('Applied OpenStreetMap changes up to ' + changes_slot, flush=True)
    except Exception as exc:
        applied_slot = 'unavailable'
        print(f'::warning::OpenStreetMap hourly changes unavailable; using the Geofabrik extract as published: {exc}', flush=True)
    clipped = root / 'regional-source.osm.pbf'
    run('osmium', 'extract', '-b', ','.join(map(str, region['bounds'])), '-s', 'complete_ways',
        source, '-o', clipped, '--overwrite')
    source.unlink()
    canonical = root / 'source.opl'
    run('osmium', 'cat', clipped, '-f', 'opl,add_metadata=false', '-o', canonical, '--overwrite')
    content = publisher.digest(canonical)
    canonical.unlink()
    checkpoint = {'content_sha256': content, 'pipeline_sha256': fingerprint,
                  'observed_source_md5': signals[region['source_url']], 'supplement_version': version,
                  'osm_changes_slot': changes_slot, 'osm_changes': applied_slot}
    prior = catalog['regions'].get(region['id'])
    if prior and prior['content_sha256'] == content and prior['pipeline_sha256'] == fingerprint and prior.get('supplement_version') == version:
        entry = {**copy.deepcopy(prior), **{k: checkpoint[k] for k in ('observed_source_md5', 'osm_changes_slot', 'osm_changes')}}
        print('Regional road content unchanged; only upstream observation advanced')
    else:
        run(sys.executable, ROOT / 'scripts/build_independent_region.py', '--region', root / 'region.json',
            '--source', clipped, '--out', root / 'out', '--work', root / 'build', '--overture-release', planned['overture_version'])
        tag = f"map-data-region-{region['id']}-r{os.environ['GITHUB_RUN_ID']}-a{os.environ['GITHUB_RUN_ATTEMPT']}"
        entry = publish_region(github, region, root / 'out', tag, checkpoint, catalog, root, promote=False)
        print('Uploaded and verified region: ' + region['id'])
    Path(args.out).write_text(json.dumps(entry, sort_keys=True))
    return entry


def merge_entries(live, entries):
    """The live catalogue with each built region's entry; a malformed entry is refused."""
    updated = copy.deepcopy(live)
    for key in OUT_OF_SCOPE:
        updated['regions'].pop(key, None)
    for entry in entries:
        key = entry.get('id')
        if not isinstance(key, str) or not ID.fullmatch(key) or key in OUT_OF_SCOPE:
            raise ValueError('Unsafe or out-of-scope region entry')
        updated['regions'][key] = entry
    return validate_catalog(updated)


def cmd_promote(args, github):
    """The one catalogue writer of an update: adds every region that built, in one change."""
    entries = [json.loads(p.read_text()) for p in sorted(Path(args.entries).glob('**/*.json'))]
    planned = json.loads(Path(args.plan).read_text())
    built = {e.get('id') for e in entries}
    failed = sorted(set(planned['regions']) - built)
    live = load_catalog(github, args.work / 'live')
    updated = merge_entries(live, entries)
    if updated != live:
        publish_catalog(github, live, updated, args.work)
    else:
        print('No changed catalogue content; no upload')
    print(f"Promoted {len(entries)} regions for {planned['changes_slot']}: " + ', '.join(sorted(built)))
    if failed:
        # Left behind the slot; the hourly watchdog starts another update for them.
        print('::warning::Not updated this time (retried by the watchdog): ' + ', '.join(failed))
    return built, failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--work', type=Path, default=Path('work/regional'))
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan', help='Choose every region this update must build')
    p.add_argument('--out', required=True)
    b = sub.add_parser('build', help='Build and upload one planned region')
    b.add_argument('--plan', required=True)
    b.add_argument('--region', required=True)
    b.add_argument('--out', required=True)
    m = sub.add_parser('promote', help='Add every built region to the catalogue at once')
    m.add_argument('--plan', required=True)
    m.add_argument('--entries', required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    github = publisher.GitHub(args.repo)
    {'plan': cmd_plan, 'build': cmd_build, 'promote': cmd_promote}[args.command](args, github)


if __name__ == '__main__':
    main()
