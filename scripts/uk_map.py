"""One detailed UK map (drivemate-v1 layout) for the app, made when any region changed.

The regions overlap and are brought up to date at different times, so their extracts
cannot simply be glued together (the same road could appear twice in two versions).
Instead the Geofabrik United Kingdom extract (Great Britain and Northern Ireland) is
brought up to the latest 01:00 / 14:00 slot with hourly OpenStreetMap changes,
then clipped back to the matching Geofabrik UK source polygon before use. This
polygon is a source boundary, not an independently verified legal border. It is made only when the regional catalogue
changed since the last UK map, published on its own immutable tag, checked, and only then
named by the pointer (ukmap.json on the map-data-ukmap release). If it fails or is too
large, the app keeps drawing the regional maps.

  uk_map.py due      writes due=true|false for the workflow
  uk_map.py changes  brings the merged extract up to the latest slot
  uk_map.py publish  checks and publishes the built map
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

import daily_schedule
import osm_changes
import progressive_regions as regions
import publish_map_data as publisher
import uk_join

POINTER_TAG = 'map-data-ukmap'
POINTER = 'ukmap.json'
MAP_NAME = 'drivemate-v1.pmtiles'
RUN_TAG = re.compile(r'map-data-ukmap-r[0-9]{1,20}-a[0-9]{1,6}')
MIN_BYTES = 300_000_000  # a whole-UK detailed map; a small file means the build failed
KEEP_MAPS = 2


def read_pointer(github, directory):
    try:
        assets = github.inventory(POINTER_TAG)
    except publisher.GitHubError as error:
        if 'HTTP 404' in str(error):
            return None
        raise
    if POINTER not in assets:
        return None
    path = github.download(POINTER_TAG, POINTER, Path(directory))
    publisher.verify_inventory(assets, {POINTER: publisher.metadata(path, POINTER_TAG)}, POINTER_TAG)
    return json.loads(path.read_text())


def due(catalog, pointer):
    """A new UK map is needed when the regional catalogue changed since the last one."""
    current = uk_join.identity(catalog)
    return bool(current) and (not pointer or pointer.get('regions') != current)


def check_size(path):
    size = Path(path).stat().st_size
    if size < MIN_BYTES:
        raise ValueError(f'UK map is only {size} bytes; the build failed')
    if size > publisher.MAX_ASSET_BYTES:
        raise ValueError(f'UK map is {size} bytes, over the {publisher.MAX_ASSET_BYTES}-byte file limit; '
                         'the app keeps the regional maps')
    return size


def pointer_document(tag, archive, region_ids, slot, changes):
    return {'schema': 1, 'built_utc': datetime.now(timezone.utc).isoformat(), 'regions': region_ids,
            'osm_changes_slot': slot, 'osm_changes': changes,
            'map': {**publisher.metadata(archive, tag), 'name': MAP_NAME, 'layout': 'drivemate-v1'}}


def validate_pointer(document):
    m = document['map']
    if (document.get('schema') != 1 or not RUN_TAG.fullmatch(str(m.get('tag'))) or m.get('name') != MAP_NAME
            or m.get('layout') != 'drivemate-v1' or len(str(m.get('sha256', ''))) != 64
            or type(m.get('bytes')) is not int or not MIN_BYTES <= m['bytes'] <= publisher.MAX_ASSET_BYTES):
        raise ValueError('Invalid UK map pointer')
    return document


def publish(github, archive, tag, directory, region_ids, slot, changes):
    """Upload to its own immutable tag, verify, then move the pointer."""
    publisher.checked_tag(tag)
    check_size(archive)
    files = {MAP_NAME: Path(archive), **publisher.licence_files()}
    publisher.upload_immutable(github, tag, files)
    document = validate_pointer(pointer_document(tag, archive, region_ids, slot, changes))
    target = Path(directory) / POINTER
    target.write_text(json.dumps(document, sort_keys=True, indent=2) + '\n')
    github.ensure_release(POINTER_TAG)
    github.run('release', 'upload', POINTER_TAG, str(target), '--repo', github.repo, '--clobber')
    publisher.verify_inventory(github.inventory(POINTER_TAG), {POINTER: publisher.metadata(target, POINTER_TAG)}, POINTER_TAG)
    return document


def prune(github, current, keep=KEEP_MAPS):
    pages = json.loads(github.run('api', '--paginate', '--slurp', f'repos/{github.repo}/releases?per_page=100'))
    tags = sorted({r['tag_name'] for page in pages for r in page if RUN_TAG.fullmatch(r.get('tag_name', ''))},
                  key=lambda t: int(re.search(r'-r([0-9]+)-', t).group(1)), reverse=True)
    for tag in tags[keep:]:
        if tag != current:
            github.run('release', 'delete', tag, '--repo', github.repo, '--yes', '--cleanup-tag')
            print('Removed old UK map release ' + tag)


def clip_uk_source(source, polygon, out, *, run=subprocess.run):
    """Fail closed on missing polygon or failed post-diff UK source extraction.

    complete_ways keeps the complete geometry of legitimate border-crossing
    ways. Geofabrik's source polygon is not a legal-border proof; verify NI
    features and independent road truth separately before release acceptance.
    """
    polygon = Path(polygon)
    out = Path(out)
    if not polygon.is_file() or polygon.stat().st_size == 0:
        raise ValueError('UK source polygon unavailable or empty; refusing map build')
    run(['osmium', 'extract', '--polygon', str(polygon), '-s', 'complete_ways',
         str(source), '-o', str(out), '--overwrite'], check=True)
    if not out.is_file() or out.stat().st_size == 0:
        raise ValueError('UK source polygon extraction produced no map; refusing publication')
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--work', type=Path, default=Path('work/uk-map'))
    sub = parser.add_subparsers(dest='command', required=True)
    d = sub.add_parser('due')
    d.add_argument('--force', action='store_true')
    c = sub.add_parser('changes')
    c.add_argument('--source', type=Path, required=True)
    c.add_argument('--out', type=Path, required=True)
    c.add_argument('--polygon', type=Path, required=True)
    p = sub.add_parser('publish')
    p.add_argument('--map', type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    github = publisher.GitHub(args.repo)
    if args.command == 'due':
        catalog = regions.load_catalog(github, args.work / 'catalog')
        result = args.force or due(catalog, read_pointer(github, args.work / 'pointer'))
        # The regions this map answers to, as they were when it was started.
        (args.work / 'regions.json').write_text(json.dumps(uk_join.identity(catalog)))
        print('UK map ' + ('due' if result else 'already made from the current regions'))
        with open(os.environ.get('GITHUB_OUTPUT', os.devnull), 'a') as out:
            out.write(f'due={"true" if result else "false"}\n')
        return
    state = args.work / 'state.json'
    if args.command == 'changes':
        slot = daily_schedule.latest_slot(datetime.now(timezone.utc))
        slot_text = slot.strftime('%Y-%m-%dT%H:%MZ')
        updated = args.work / 'updated-world.osm.pbf'
        clip_input = args.source
        try:
            changes = osm_changes.ensure(github, slot, args.work / 'changes')
            osm_changes.apply(args.source, changes, updated)
            clip_input = updated
            applied = slot_text
            print('Applied OpenStreetMap changes up to ' + slot_text)
        except Exception as exc:
            updated.unlink(missing_ok=True)
            print(f'::warning::OpenStreetMap hourly changes unavailable; using the UK extract as published: {exc}')
            applied = 'unavailable'
        # This MUST be outside the optional-update exception handler. A missing
        # or failed polygon clip is a hard build failure, never an unscoped fallback.
        clip_uk_source(clip_input, args.polygon, args.out)
        updated.unlink(missing_ok=True)
        state.write_text(json.dumps({'slot': slot_text, 'changes': applied}))
        return
    region_ids = json.loads((args.work / 'regions.json').read_text())
    info = json.loads(state.read_text()) if state.exists() else {'slot': None, 'changes': 'unavailable'}
    tag = f"{POINTER_TAG}-r{os.environ['GITHUB_RUN_ID']}-a{os.environ['GITHUB_RUN_ATTEMPT']}"
    document = publish(github, args.map, tag, args.work, region_ids, info['slot'], info['changes'])
    print(f"Published UK map {tag}: {document['map']['bytes']:,} bytes")
    try:
        prune(github, tag)
    except Exception as exc:
        print(f'::warning::Old UK map releases not removed: {exc}')


if __name__ == '__main__':
    main()
