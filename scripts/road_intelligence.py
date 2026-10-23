"""Collect bounded official transport snapshots, separately from offline road rules.

A nightly snapshot is not a live traffic service. Readers must honour expires_utc
and each source's status; these unnormalised pages must not control routing.
"""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

NH = 'https://api.data.nationalhighways.co.uk'
LIMIT = 20_000_000
MAX_PAGES = 40


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Source redirect rejected')


def approved_url(url, origin, path=None):
    value, allowed = urlsplit(url), urlsplit(origin)
    if (value.scheme != 'https' or value.netloc != allowed.netloc
            or value.username or value.password or value.fragment
            or (path is not None and value.path != path)):
        raise ValueError('Unapproved source or pagination URL')
    return url


def request_json(url, headers):
    # No automatic redirects: subscription credentials never follow another host.
    request = Request(url, headers={**headers, 'User-Agent': 'DriveMate-map/1'})
    with build_opener(NoRedirects()).open(request, timeout=45) as response:
        if response.status != 200:
            raise ValueError('Unexpected source status')
        raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('Source page exceeds limit')
        return json.loads(raw), response.headers.get('x-next', '')


def nh_pages(url, key, fetch=request_json, pause=time.sleep):
    path = urlsplit(url).path
    seen, pages, total = set(), [], 0
    while url:
        approved_url(url, NH, path)
        if url in seen or len(seen) >= MAX_PAGES:
            raise ValueError('Repeated or excessive source pagination')
        seen.add(url)
        # Shared across the four collections: stay below the official 10/minute limit.
        pause(6.2)
        page, following = fetch(url, {'Ocp-Apim-Subscription-Key': key,
                                      'X-Response-MediaType': 'application/json'})
        if not isinstance(page, dict) or not isinstance(page.get('D2Payload'), dict):
            raise ValueError('Unexpected National Highways response schema')
        payload = page['D2Payload']
        if not isinstance(payload.get('situation'), list):
            raise ValueError('Missing situation collection')
        total += len(json.dumps(payload).encode())
        if total > 100_000_000:
            raise ValueError('Source collection exceeds limit')
        pages.append(payload)
        url = approved_url(urljoin(url, following), NH, path) if following else ''
    return pages


def collect(now, key, fetch=request_json, pause=time.sleep):
    if now.tzinfo is None:
        raise ValueError('Collection timestamp needs a timezone')
    stamp = now.astimezone(timezone.utc)
    document = {'schema': 1, 'kind': 'dated transport snapshot',
                'retrieved_utc': stamp.isoformat(),
                'expires_utc': (stamp + timedelta(minutes=10)).isoformat(),
                'routing_authoritative': False, 'sources': [],
                'limitations': ['Nightly snapshots are not live traffic.',
                    'No app display or routing integration is implied by collection.',
                    'Availability of a source does not imply every event is active.']}
    start = (stamp - timedelta(hours=6)).strftime('%Y-%m-%dT%H:%M:%S')
    end = (stamp + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%S')
    for name, path, parameter in [('closures', '/roads/v2.0/closures', 'closureType'),
                                  ('speed-managed-areas', '/sma/v1.0/speedManagedAreas', 'speedRestrictionType')]:
        for kind in ('planned', 'unplanned'):
            item = {'id': 'national-highways-' + name + '-' + kind,
                    'origin': NH + path, 'coverage': 'England strategic road network',
                    'attribution': 'Powered by National Highways’ Transport Data Feeds',
                    'terms': 'https://developer.data.nationalhighways.co.uk/terms',
                    'status': 'setup_required', 'pages': []}
            if key:
                try:
                    query = urlencode({parameter: kind, 'startDateTime': start, 'endDateTime': end})
                    item['pages'] = nh_pages(NH + path + '?' + query, key, fetch, pause)
                    item['status'] = 'collected'
                except Exception:
                    # Do not log exception bodies: API responses may reflect credentials.
                    item['status'] = 'unavailable'
            document['sources'].append(item)
    document['complete'] = all(item['status'] == 'collected' for item in document['sources'])
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    document = collect(datetime.now(timezone.utc), os.environ.get('NATIONAL_HIGHWAYS_API_KEY', '').strip())
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / 'road-intelligence.json.gz'
    path.write_bytes(gzip.compress(json.dumps(document, sort_keys=True).encode(), mtime=0))
    manifest = {'schema': 1, 'retrieved_utc': document['retrieved_utc'],
                'expires_utc': document['expires_utc'], 'routing_authoritative': False,
                'complete': document['complete'], 'sources': [
                    {k: item[k] for k in ('id', 'status', 'attribution', 'terms')} for item in document['sources']],
                'file': path.name, 'bytes': path.stat().st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (args.out / 'road-intelligence-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'complete': document['complete'], 'sources': manifest['sources']}))
    # Missing user setup is explicit in the manifest; configured failures fail CI.
    if any(item['status'] == 'unavailable' for item in document['sources']):
        raise SystemExit('Configured transport source unavailable; previous publication retained')


if __name__ == '__main__':
    main()
