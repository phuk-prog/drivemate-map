"""Join every published region's guidance data into one UK set the app reads as a whole.

The regions are built and published on their own, twice a day when they change
(progressive_regions.py). The app used to read lane, speed-limit, road-information,
road-detail and place squares only from the one region it was in, so a route into the
next county, or a square cut by a county line, had gaps. This job merges each square
from every region that has it, without duplicates (the regions overlap), and publishes
the result on its own immutable tags with a small pointer (uk.json on the map-data-ukjoin
release). Map pictures stay regional; the app already draws neighbouring regions.

A square is only rebuilt when one of its regional copies changed: the rest are reused
from the last join. Nothing is published when no region changed. The pointer moves only
after every file is uploaded and verified, so a failed join leaves the last good one.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

import progressive_regions as regions
import publish_map_data as publisher

POINTER_TAG = 'map-data-ukjoin'
POINTER = 'uk.json'
RUN_TAG = re.compile(r'map-data-ukjoin-r[0-9]{1,20}-a[0-9]{1,6}(-part[0-9]{3})?')
SQUARE = re.compile(r'(lanes|limits|roadinfo|road-details|places)--?[0-9]{1,4}_-?[0-9]{1,4}\.json(\.gz)?')
SEARCH = 'search-offline-uk.tsv.gz'
UK_WIDE = ('cameras-uk.json', 'charge-zones-uk.json', SEARCH)
SEARCH_HEADER = '#drivemate-search-offline'
# National postcode lines (scripts/postcodes_uk.py output), joined into the search list when set.
NATIONAL_POSTCODES = None
KEEP_JOINS = 3
# How many UK roads carry each extra driving tag (the app keeps the same ones: RoadDetails.kt),
# so the owner can see which information OpenStreetMap maps well before a category relies on it.
COVERAGE_TAGS = ('maxheight', 'maxwidth', 'maxweight', 'maxlength', 'hgv', 'access:conditional',
                 'motor_vehicle:conditional', 'oneway:conditional', 'maxspeed:conditional', 'maxspeed:variable',
                 'maxspeed:type', 'overtaking', 'incline', 'surface', 'smoothness', 'ford', 'toll', 'hov:lanes',
                 'lanes', 'turn:lanes', 'maxspeed', 'lit', 'bridge', 'tunnel')


def joinable(name):
    return bool(SQUARE.fullmatch(name)) or name in UK_WIDE


def identity(catalog):
    """Which regional publication this join is made from: each region's manifest digest."""
    return {key: entry['manifest']['sha256'] for key, entry in sorted(catalog['regions'].items())}


def sources_key(copies):
    """Cache by newest-first precedence; only overlapping squares need v2 rebuild."""
    digests = [c['sha256'] for c in copies]
    # merge() keeps the first regional road copy. Sorting digests loses that
    # priority; version the multi-source key so legacy sorted keys cannot
    # silently reuse a join made with the opposite region winning.
    if len(digests) > 1:
        digests.insert(0, 'priority-v2')
    return hashlib.sha256(json.dumps(digests).encode()).hexdigest()


# Bump when merge_search output changes for the same regional inputs, so the join rebuilds it.
SEARCH_JOIN_VERSION = 'search-v3-national-postcodes'


def join_key(name, copies):
    """Reuse key: the regional sources, plus the merge version for the search list."""
    key = sources_key(copies)
    if name == SEARCH:
        if NATIONAL_POSTCODES:
            digest = hashlib.sha256(Path(NATIONAL_POSTCODES).read_bytes()).hexdigest()
            key = f'{key}:postcodes:{digest}'
        key = hashlib.sha256(f'{SEARCH_JOIN_VERSION}:{key}'.encode()).hexdigest()
    return key


def _unique(items, key):
    seen, out = set(), []
    for item in items:
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out


def merge_search(documents):
    """All regions' offline-search lines as one UK list (C08).

    Each region publishes only its own area's postcodes and names, so a phone searching from
    Stockport could not find ST4 8JG. The union keeps every distinct line, sorted in the same
    plain order the app binary-searches; one header carries the newest date and every credit.
    """
    lines, dates, credits = set(), [], []
    for doc in documents:
        header, rows = doc
        parts = header.split('\t')
        if len(parts) < 4 or parts[0] != SEARCH_HEADER or parts[1] != '1':
            raise ValueError('Unexpected offline search header')
        dates.append(parts[2])
        for credit in '\t'.join(parts[3:]).split('; '):
            if credit and credit not in credits:
                credits.append(credit)
        lines.update(rows)
    header = '\t'.join([SEARCH_HEADER, '1', max(dates), '; '.join(credits)])
    return header, sorted(one_point_per_postcode(lines))


def one_point_per_address(groups, within_m=200.0):
    """Copies of the same address label (type O) within [within_m] collapse to their mean point.

    Regions add one point per address entrance/building part; the same label far apart (two
    different places) is kept separately. Single points keep their original line unchanged.
    """
    import math
    out = []
    for (key, name, area), points in groups.items():
        if len(points) == 1:
            out.append(points[0][2])
            continue
        clusters = []
        for lat, lon, line in sorted(points):
            for c in clusters:
                dy = (lat - c[0]) * 111_320.0
                dx = (lon - c[1]) * 111_320.0 * math.cos(math.radians(lat))
                if dx * dx + dy * dy <= within_m * within_m:
                    c[2].append((lat, lon, line))
                    break
            else:
                clusters.append([lat, lon, [(lat, lon, line)]])
        for _, _, members in clusters:
            if len(members) == 1:
                out.append(members[0][2])
            else:
                lat = sum(m[0] for m in members) / len(members)
                lon = sum(m[1] for m in members) / len(members)
                out.append('\t'.join([key, 'O', name, area, f'{lat:.5f}', f'{lon:.5f}']))
    return out


def one_point_per_postcode(lines):
    """Postcode (type P) lines collapse to one per postcode.

    Some regions add one point per address (no area, 5 decimals): up to hundreds per postcode, 1.7M
    extra lines UK-wide. The app uses one point per postcode, so keep the named centroid line when
    there is one, otherwise the mean of the address points. Address labels (type O) collapse only
    within 200 m (one_point_per_address). Other line types are untouched.
    """
    out, groups, others = [], {}, {}
    for line in lines:
        key, kind, rest = line.split('\t', 2)
        if kind == 'P':
            groups.setdefault(key, []).append(line)
        elif kind == 'O':
            f = line.split('\t')
            others.setdefault((f[0], f[2], f[3]), []).append((float(f[4]), float(f[5]), line))
        else:
            out.append(line)
    out.extend(one_point_per_address(others))
    for key, group in groups.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        named = sorted(g for g in group if g.split('\t')[3])
        if named:
            out.append(named[0])
            continue
        fields = [g.split('\t') for g in sorted(group)]
        lat = sum(float(f[4]) for f in fields) / len(fields)
        lon = sum(float(f[5]) for f in fields) / len(fields)
        out.append('\t'.join([key, 'P', fields[0][2], '', f'{lat:.5f}', f'{lon:.5f}']))
    return out


def merge(name, documents):
    """One square from all its regional copies, newest region first; the first copy of a road wins."""
    if not documents:
        raise ValueError('Nothing to merge: ' + name)
    if name == SEARCH:
        return merge_search(documents)
    if name.startswith('places-'):
        rows = [row for doc in documents for row in doc]
        return _unique(rows, lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))
    first = documents[0]
    if not isinstance(first, dict):
        raise ValueError('Unexpected file layout: ' + name)
    merged = {k: v for k, v in first.items() if k not in ('ways', 'elements', 'zones')}
    if 'ways' in first:
        ways = [w for doc in documents for w in doc.get('ways', [])]
        if all(isinstance(w, dict) and 'id' in w for w in ways):
            merged['ways'] = _unique(ways, lambda w: w['id'])
        else:  # speed limits: [limit, line] without OSM ids; identical copies are the same road
            merged['ways'] = _unique(ways, lambda w: json.dumps(w))
    if 'elements' in first:
        elements = [e for doc in documents for e in doc.get('elements', [])]
        merged['elements'] = _unique(elements, lambda e: (e.get('type'), e.get('id')))
    if 'zones' in first:
        zones = [z for doc in documents for z in doc.get('zones', [])]
        merged['zones'] = _unique(zones, lambda z: json.dumps(z, sort_keys=True))
    return merged


def tag_counts(document):
    """Roads carrying each coverage tag in one road-detail square, plus the number of roads."""
    ways = document.get('ways', []) if isinstance(document, dict) else []
    counts = {'roads': len(ways)}
    for tag in COVERAGE_TAGS:
        counts[tag] = sum(1 for w in ways if tag in w.get('t', {}))
    return counts


def coverage(files):
    """UK totals from every road-detail square's counts."""
    total = {}
    for name, entry in files.items():
        if name.startswith('road-details-'):
            for key, value in entry.get('tags', {}).items():
                total[key] = total.get(key, 0) + value
    return dict(sorted(total.items()))


def read(path):
    if Path(path).name == SEARCH:
        with gzip.open(path, 'rt', encoding='utf-8', newline='\n') as f:
            header, *rows = f.read().split('\n')
        rows = [r for r in rows if r]
        if not header.startswith(SEARCH_HEADER + '\t') or any(r.startswith('#') or r.count('\t') != 5 for r in rows):
            raise ValueError('Invalid offline search list')
        if rows != sorted(rows):
            raise ValueError('Offline search list is not sorted for binary search')
        return header, rows
    with (gzip.open(path, 'rt', encoding='utf-8') if path.name.endswith('.gz') else path.open(encoding='utf-8')) as f:
        return json.load(f)


def write(path, document):
    if path.name == SEARCH:
        header, rows = document
        text = ('\n'.join([header, *rows]) + '\n').encode('utf-8')
        with path.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0, compresslevel=9) as out:
            out.write(text)
        return
    text = json.dumps(document, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if path.name.endswith('.gz'):
        # No file name or clock time in the gzip header, so equal content gives equal bytes.
        with path.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as out:
            out.write(text)
    else:
        path.write_bytes(text)


def load_manifests(github, catalog, directory):
    """Every region's verified manifest, newest publication first."""
    entries = sorted(catalog['regions'].values(), key=lambda e: e.get('published_utc', ''), reverse=True)
    result = []
    for entry in entries:
        info = entry['manifest']
        target = Path(directory) / entry['id']
        target.mkdir(parents=True, exist_ok=True)
        path = github.download(info['tag'], 'manifest.json', target)
        if path.stat().st_size != info['bytes'] or publisher.digest(path) != info['sha256']:
            raise ValueError('Regional manifest integrity failed: ' + entry['id'])
        result.append((entry['id'], json.loads(path.read_text())['files']))
    return result


def plan(manifests):
    """For each joinable file name, its regional copies in region order."""
    copies = {}
    for region_id, files in manifests:
        for name, asset in files.items():
            if joinable(name):
                copies.setdefault(name, []).append({**asset, 'region': region_id})
    return dict(sorted(copies.items()))


def load_pointer(github, directory):
    try:
        assets = github.inventory(POINTER_TAG)
    except publisher.GitHubError as error:
        if 'HTTP 404' in str(error):
            return None, {}
        raise
    if POINTER not in assets:
        return None, {}
    path = github.download(POINTER_TAG, POINTER, Path(directory))
    publisher.verify_inventory(assets, {POINTER: publisher.metadata(path, POINTER_TAG)}, POINTER_TAG)
    pointer = json.loads(path.read_text())
    info = pointer['manifest']
    if not RUN_TAG.fullmatch(info['tag']):
        raise ValueError('Invalid UK join manifest tag')
    old = github.download(info['tag'], 'manifest.json', Path(directory) / 'previous')
    if old.stat().st_size != info['bytes'] or publisher.digest(old) != info['sha256']:
        raise ValueError('Previous UK join manifest integrity failed')
    return pointer, json.loads(old.read_text())['files']


def build(github, wanted, previous_files, work):
    """Each joined file: reused when its regional copies are unchanged, else merged afresh."""
    work = Path(work)
    built, reused = {}, {}
    todo = []
    for name, copies in wanted.items():
        key = join_key(name, copies)
        old = previous_files.get(name)
        if old and old.get('sources') == key and RUN_TAG.fullmatch(old.get('tag', '')):
            reused[name] = old
        else:
            todo.append((name, copies, key))

    def one(job):
        name, copies, key = job
        folder = work / 'parts' / name
        folder.mkdir(parents=True, exist_ok=True)
        documents = []
        for i, copy in enumerate(copies):
            part = folder / str(i)
            part.mkdir(exist_ok=True)
            path = github.download(copy['tag'], name, part)
            if path.stat().st_size != copy['bytes'] or publisher.digest(path) != copy['sha256']:
                raise ValueError(f'Regional file integrity failed: {copy["region"]}/{name}')
            documents.append(read(path))
        if name == SEARCH and NATIONAL_POSTCODES:
            documents.append(read(Path(NATIONAL_POSTCODES)))
        out = work / 'out' / name
        merged = merge(name, documents)
        write(out, merged)
        read(out)  # the joined file must load as the app loads it
        shutil.rmtree(folder)
        return name, out, key, (tag_counts(merged) if name.startswith('road-details-') else None)

    (work / 'out').mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, out, key, counts in pool.map(one, todo):
            built[name] = (out, key, counts)
    return built, reused


def publish(github, built, reused, run_tag, region_ids, work):
    """Upload new files in shards of under 1,000, the manifest last, then move the pointer."""
    publisher.checked_tag(run_tag)
    licences = publisher.licence_files()
    pending = {name: item[0] for name, item in built.items()}
    pending.update(licences)
    names = sorted(pending)
    limit = min(publisher.MAIN_FILES, publisher.ASSET_LIMIT - 1)
    groups = [names[i:i + limit] for i in range(0, len(names), limit)] or [[]]
    files = dict(reused)
    shards = []
    for idx, group in enumerate(groups):
        tag = run_tag if idx == 0 else f'{run_tag}-part{idx + 1:03d}'
        shards.append((tag, {n: pending[n] for n in group}))
        for n in group:
            entry = publisher.metadata(pending[n], tag)
            if n in built:
                entry['sources'] = built[n][1]
                if built[n][2] is not None:
                    entry['tags'] = built[n][2]
            files[n] = entry
    manifest = {'schema': 1, 'kind': 'uk-join', 'regions': region_ids, 'files': dict(sorted(files.items())),
                'tag_coverage': coverage(files)}
    manifest_path = Path(work) / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + '\n')
    if manifest_path.stat().st_size > publisher.MAX_MANIFEST_BYTES:
        raise ValueError('UK join manifest exceeds the app download limit')
    shards[0][1]['manifest.json'] = manifest_path
    for tag, shard in shards:
        publisher.upload_immutable(github, tag, shard)
    for tag in {e['tag'] for e in reused.values()}:
        wanted = {n: {k: v for k, v in e.items() if k in ('sha256', 'bytes', 'tag')} for n, e in reused.items() if e['tag'] == tag}
        publisher.verify_inventory(github.inventory(tag), wanted, tag)
    pointer = {'schema': 1, 'built_utc': datetime.now(timezone.utc).isoformat(), 'regions': region_ids,
               'manifest': publisher.metadata(manifest_path, run_tag)}
    target = Path(work) / POINTER
    target.write_text(json.dumps(pointer, sort_keys=True, indent=2) + '\n')
    github.ensure_release(POINTER_TAG)
    github.run('release', 'upload', POINTER_TAG, str(target), '--repo', github.repo, '--clobber')
    publisher.verify_inventory(github.inventory(POINTER_TAG), {POINTER: publisher.metadata(target, POINTER_TAG)}, POINTER_TAG)
    return manifest


def prune(github, manifest, keep=KEEP_JOINS):
    """Old join releases no file still points to, beyond the newest few, are removed."""
    used = {e['tag'] for e in manifest['files'].values()}
    pages = json.loads(github.run('api', '--paginate', '--slurp', f'repos/{github.repo}/releases?per_page=100'))
    tags = sorted({r['tag_name'] for page in pages for r in page if RUN_TAG.fullmatch(r.get('tag_name', ''))},
                  key=lambda t: int(re.search(r'-r([0-9]+)-', t).group(1)), reverse=True)
    runs = []
    for tag in tags:
        run = tag.split('-part')[0]
        if run not in runs:
            runs.append(run)
    recent = set(runs[:keep])
    for tag in tags:
        if tag not in used and tag.split('-part')[0] not in recent:
            github.run('release', 'delete', tag, '--repo', github.repo, '--yes', '--cleanup-tag')
            print('Removed old UK join release ' + tag)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--work', type=Path, default=Path('work/uk-join'))
    parser.add_argument('--force', action='store_true', help='Join even when no region changed')
    parser.add_argument('--postcodes', type=Path, default=None, help='National postcode lines to add to the search list')
    args = parser.parse_args()
    global NATIONAL_POSTCODES
    NATIONAL_POSTCODES = args.postcodes
    args.work.mkdir(parents=True, exist_ok=True)
    github = publisher.GitHub(args.repo)
    catalog = regions.load_catalog(github, args.work / 'catalog')
    if not catalog['regions']:
        print('No published regions yet; nothing to join')
        return
    region_ids = identity(catalog)
    pointer, previous_files = load_pointer(github, args.work / 'pointer')
    if pointer and pointer.get('regions') == region_ids and not args.force:
        print(f'UK join already up to date with all {len(region_ids)} regions')
        return
    wanted = plan(load_manifests(github, catalog, args.work / 'manifests'))
    built, reused = build(github, wanted, previous_files, args.work)
    print(f'UK join: {len(wanted)} files from {len(region_ids)} regions; {len(built)} rebuilt, {len(reused)} unchanged')
    run_tag = f"{POINTER_TAG}-r{os.environ['GITHUB_RUN_ID']}-a{os.environ['GITHUB_RUN_ATTEMPT']}"
    manifest = publish(github, built, reused, run_tag, region_ids, args.work)
    print('Published UK join ' + run_tag)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as s:
            s.write(f'UK join {run_tag}: {len(wanted)} files from {len(region_ids)} regions '
                    f'({len(built)} rebuilt, {len(reused)} unchanged)\n\n')
            roads = manifest['tag_coverage'].get('roads', 0)
            s.write('| OpenStreetMap tag | UK roads with it | Share |\n|---|---|---|\n')
            for tag, count in manifest['tag_coverage'].items():
                if tag != 'roads':
                    s.write(f'| {tag} | {count:,} | {100 * count / roads:.1f}% |\n' if roads else f'| {tag} | {count:,} | |\n')
    try:
        prune(github, manifest)
    except Exception as exc:  # tidying never fails a good join
        print(f'::warning::Old UK join releases not removed: {exc}')


if __name__ == '__main__':
    main()
