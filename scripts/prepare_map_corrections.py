"""Prepare a curated, immutable shared-map release. Does not upload or copy provider data."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import time

MAX_BYTES = 2 * 1024 * 1024
MAX_AGE = 180 * 24 * 60 * 60 * 1000
LICENCES = {'ODbL-1.0', 'OGL-UK-3.0', 'CC0-1.0', 'DriveMate-observation'}
INDICATIONS = {'left', 'right', 'straight', 'slight left', 'slight right', 'sharp left', 'sharp right', 'uturn'}


def number(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('Invalid numeric field')


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid integer field')


def point(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('Expected longitude, latitude')
    number(value[0], -11, 3)
    number(value[1], 49, 62)


def distance(a, b):
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(math.radians(b[0]-a[0])/2)**2
    return 6371000 * 2 * math.atan2(math.sqrt(h), math.sqrt(1-h))


def locate(path, p):
    """Match the app's short-segment projection and reject ambiguous loop passes."""
    along = 0
    hits = []
    for a, b in zip(path, path[1:]):
        length = distance(a, b)
        m_per_lon = 111320 * math.cos(math.radians(p[1]))
        ax, ay = (a[0]-p[0])*m_per_lon, (a[1]-p[1])*110540
        bx, by = (b[0]-p[0])*m_per_lon, (b[1]-p[1])*110540
        dx, dy = bx-ax, by-ay
        t = max(0, min(1, (-ax*dx-ay*dy)/(dx*dx+dy*dy)))
        if math.hypot(ax+t*dx, ay+t*dy) < 4:
            hits.append(along+t*length)
        along += length
    if not hits or max(hits)-min(hits) > 12:
        raise ValueError('Lane location is outside its corridor or ambiguous')


def validate_record(r, now):
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', r['id']):
        raise ValueError('Invalid correction ID')
    if not re.fullmatch(r'[A-Za-z0-9:._-]{1,100}', r['road_id']):
        raise ValueError('Missing road binding')
    source = r['source']
    if source['licence'] not in LICENCES or source['reuse_permitted'] is not True:
        raise ValueError('Source rights have not been confirmed')
    for name in ('reference', 'attribution'):
        if not isinstance(source[name], str) or not source[name].strip() or len(source[name]) > 500:
            raise ValueError('Missing source evidence')
    integer(r['verified_at'], 1, now)
    integer(r['expires_at'], now + 1, r['verified_at'] + MAX_AGE)
    if 'conditions' in r:
        raise ValueError('Conditional corrections require an interpreter')
    path = r['path']
    if not isinstance(path, list) or not 2 <= len(path) <= 200:
        raise ValueError('Missing road geometry')
    for p in path:
        point(p)
    if any(not 2 <= distance(a, b) <= 500 for a, b in zip(path, path[1:])):
        raise ValueError('Invalid road geometry spacing')
    if r['kind'] == 'speed_limit':
        number(r['kmh'], 5, 160)
    elif r['kind'] == 'lanes':
        for key in ('at', 'maneuver_at'):
            point(r[key])
        locate(path, r['at'])
        for key in ('approach_heading', 'exit_heading'):
            number(r[key], 0, 360)
            if r[key] == 360:
                raise ValueError('Invalid heading')
        if r['placement'] not in ('approach', 'stage'):
            raise ValueError('Invalid lane stage')
        if r['maneuver'] != 'roundabout' and not re.fullmatch(r'[a-z ]{1,30}\|[a-z ]{1,30}', r['maneuver']):
            raise ValueError('Missing manoeuvre binding')
        integer(r['exit'], 1 if r['maneuver'] == 'roundabout' else 0, 32)
        lanes = r['lanes']
        if not isinstance(lanes, list) or not 1 <= len(lanes) <= 12:
            raise ValueError('Invalid lanes')
        for lane in lanes:
            if not isinstance(lane, list) or not 1 <= len(lane) <= 8 or not all(i in INDICATIONS for i in lane):
                raise ValueError('Invalid lane indications')
        valid = r['valid_lanes']
        if not isinstance(valid, list) or not valid:
            raise ValueError('No valid lane')
        for lane in valid:
            integer(lane, 0, len(lanes)-1)
        preferred = r['preferred_lane']
        if preferred is not None:
            integer(preferred, 0, len(lanes)-1)
            if preferred not in valid:
                raise ValueError('Preferred lane is not usable')
    else:
        raise ValueError('Unsupported correction kind')


def prepare(document, version, now, allow_empty=False):
    if (not isinstance(document, dict) or type(document.get('schema')) is not int or
            document['schema'] != 1 or not isinstance(version, str) or len(version) > 80 or
            not re.fullmatch(r'map-corrections-[0-9A-Za-z._-]+', version)):
        raise ValueError('Invalid schema or version')
    records = document['records']
    if not isinstance(records, list) or len(records) > 1000:
        raise ValueError('Invalid record count')
    ids = set()
    active = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', record['id']):
            raise ValueError('Invalid correction ID')
        if record.get('status') not in ('draft', 'verified', 'withdrawn'):
            raise ValueError('Unknown review status')
        if record['id'] in ids:
            raise ValueError('Duplicate correction ID')
        ids.add(record['id'])
        if record['status'] != 'verified':
            continue
        validate_record(record, now)
        # Private notes, screenshot references, review traces and unrelated fields are excluded.
        fields = ('id', 'kind', 'status', 'road_id', 'source', 'verified_at', 'expires_at', 'path')
        if record['kind'] == 'lanes':
            fields += ('at', 'maneuver_at', 'approach_heading', 'exit_heading', 'maneuver', 'exit',
                       'placement', 'lanes', 'valid_lanes', 'preferred_lane')
        else:
            fields += ('kmh',)
        output = {key: record[key] for key in fields}
        output['source'] = {key: record['source'][key] for key in ('licence', 'reuse_permitted', 'reference', 'attribution')}
        active.append(output)
    if not active and not allow_empty:
        raise ValueError('No verified corrections; use --allow-empty only for an intentional empty release')
    payload = (json.dumps({'schema': 1, 'version': version, 'records': active}, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + '\n').encode()
    if len(payload) > MAX_BYTES:
        raise ValueError('Correction release is too large')
    pointer = {'schema': 1, 'tag': version, 'sha256': hashlib.sha256(payload).hexdigest()}
    return payload, pointer


def stage_release(out, payload, pointer):
    """Prepare both assets before making a new release directory visible."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise FileExistsError('Release directory already exists')
    # A per-target reservation prevents two publishers from replacing one another.
    reservation = out.parent / ('.' + out.name + '.publishing')
    with reservation.open('x'):
        try:
            with tempfile.TemporaryDirectory(prefix='.' + out.name + '-', dir=out.parent) as temporary:
                staged = Path(temporary) / 'release'
                staged.mkdir()
                (staged / 'corrections.json').write_bytes(payload)
                (staged / 'latest.json').write_text(json.dumps(pointer, indent=2) + '\n', encoding='utf-8')
                if out.exists():
                    raise FileExistsError('Release directory already exists')
                staged.rename(out)
        finally:
            reservation.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--allow-empty', action='store_true')
    args = parser.parse_args()
    if args.input.stat().st_size > MAX_BYTES:
        parser.error('Input is too large')
    try:
        payload, pointer = prepare(json.loads(args.input.read_text()), args.version, int(time.time()*1000), args.allow_empty)
    except (ValueError, KeyError, TypeError):
        parser.error('Corrections failed validation; check status, road binding, dates, geometry and source rights')
    try:
        stage_release(args.out, payload, pointer)
    except OSError:
        parser.error('Could not stage release; choose a new output directory and check filesystem access')
    print(f"Prepared {len(json.loads(payload)['records'])} verified corrections. Nothing uploaded.")


if __name__ == '__main__':
    main()
