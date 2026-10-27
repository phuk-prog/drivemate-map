"""Hourly check that the twice-daily map update has not stalled; restarts it if it has.

The regional update is a chain: each run builds one region and starts the next. GitHub may
drop or delay a scheduled start (a 01:00 start ran at 05:31 on 2026-10-25 and was then
cancelled), and a failure before a region is chosen ends the chain. This check lives in its
own workflow, so it never takes the update's place in GitHub's queue. It starts the update
only when no update run is running or waiting and the catalogue still has regions behind the
latest 01:00 / 14:00 slot, the overview only when it is older than that slot, and the UK
join (one UK set of guidance data from every region) once the regions are done and it was
made from an older catalogue.
"""
import argparse
from datetime import datetime, timezone
import json
import subprocess
from urllib.request import urlopen

import daily_schedule

CATALOG = 'https://github.com/{repo}/releases/download/map-regions/catalog.json'
OVERVIEW = 'https://github.com/{repo}/releases/download/map-data-overview/overview.json'
UK_JOIN = 'https://github.com/{repo}/releases/download/map-data-ukjoin/uk.json'
UK_MAP = 'https://github.com/{repo}/releases/download/map-data-ukmap/ukmap.json'
DMN_INDEX = 'https://github.com/{repo}/releases/download/dmn-uk/dmn-uk.json'
# A region is retried once an hour at most; one that keeps failing is listed, not hidden.
REGIONS_WORKFLOW = 'regional-maps.yml'
OVERVIEW_WORKFLOW = 'uk-overview.yml'
JOIN_WORKFLOW = 'uk-join.yml'
MAP_WORKFLOW = 'uk-map.yml'
DMN_WORKFLOW = 'dmn-pack.yml'


def behind(catalog, slot_text):
    """Regions not yet built for this slot."""
    return sorted(k for k, r in catalog.get('regions', {}).items() if r.get('osm_changes_slot') != slot_text)


def overview_stale(pointer, slot):
    built = pointer.get('built_utc') if pointer else None
    if not built:
        return True
    return datetime.fromisoformat(built) < slot


def join_stale(catalog, join_pointer):
    """The UK join was made from a different set of regional publications than the live one."""
    regions = {k: r.get('manifest', {}).get('sha256') for k, r in sorted(catalog.get('regions', {}).items())}
    return bool(regions) and (not join_pointer or join_pointer.get('regions') != regions)


def decide(active_regions, active_overview, catalog, pointer, now, active_join=False, join_pointer=None, check_join=False,
           active_map=False, map_pointer=None, check_map=False,
           active_dmn=False, dmn_index=None, check_dmn=False):
    """What to start now: a list of workflow files (possibly empty), and why."""
    slot = daily_schedule.latest_slot(now)
    slot_text = slot.strftime('%Y-%m-%dT%H:%MZ')
    start, notes = [], []
    late = behind(catalog, slot_text)
    if active_regions:
        notes.append('regional update running')
    elif late:
        start.append(REGIONS_WORKFLOW)
        notes.append(f'{len(late)} regions behind {slot_text}: ' + ', '.join(late[:12]))
    else:
        notes.append(f'every region up to {slot_text}')
    if active_overview:
        notes.append('overview running')
    elif overview_stale(pointer, slot):
        start.append(OVERVIEW_WORKFLOW)
        notes.append(f'overview older than {slot_text}')
    if check_join:
        if active_join:
            notes.append('UK join running')
        elif active_regions or late:
            notes.append('UK join waits for the regions')
        elif join_stale(catalog, join_pointer):
            start.append(JOIN_WORKFLOW)
            notes.append('UK join older than the regional catalogue')
        else:
            notes.append('UK join up to date')
    if check_map:
        # The same rule as the join: once the regions are done, made from the current catalogue.
        if active_map:
            notes.append('UK map running')
        elif active_regions or late:
            notes.append('UK map waits for the regions')
        elif join_stale(catalog, map_pointer):
            start.append(MAP_WORKFLOW)
            notes.append('UK map older than the regional catalogue')
        else:
            notes.append('UK map up to date')
    if check_dmn:
        index = {'regions': dmn_index.get('catalog_regions')} if dmn_index else None
        if active_dmn:
            notes.append('DMN road pieces running')
        elif active_regions or late:
            notes.append('DMN road pieces wait for the regions')
        elif join_stale(catalog, index):
            start.append(DMN_WORKFLOW)
            notes.append('DMN road pieces older than the regional catalogue')
        else:
            notes.append('DMN road pieces up to date')
    return start, notes


def gh(*args):
    return subprocess.run(['gh', *args], check=True, capture_output=True, text=True).stdout


def active(repo, workflow):
    runs = json.loads(gh('api', f'repos/{repo}/actions/workflows/{workflow}/runs?per_page=20'))['workflow_runs']
    return any(r['status'] in ('queued', 'in_progress', 'waiting', 'pending', 'requested') for r in runs)


def read(url):
    try:
        with urlopen(url, timeout=60) as response:
            return json.loads(response.read(3_000_000))
    except Exception as exc:
        print(f'::warning::Could not read {url}: {exc}')
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    args = parser.parse_args()
    catalog = read(CATALOG.format(repo=args.repo)) or {'regions': {}}
    pointer = read(OVERVIEW.format(repo=args.repo))
    join_pointer = read(UK_JOIN.format(repo=args.repo))
    map_pointer = read(UK_MAP.format(repo=args.repo))
    dmn_index = read(DMN_INDEX.format(repo=args.repo))
    start, notes = decide(active(args.repo, REGIONS_WORKFLOW), active(args.repo, OVERVIEW_WORKFLOW),
                          catalog, pointer, datetime.now(timezone.utc),
                          active_join=active(args.repo, JOIN_WORKFLOW), join_pointer=join_pointer, check_join=True,
                          active_map=active(args.repo, MAP_WORKFLOW), map_pointer=map_pointer, check_map=True,
                          active_dmn=active(args.repo, DMN_WORKFLOW), dmn_index=dmn_index, check_dmn=True)
    for note in notes:
        print(note)
    for workflow in start:
        gh('workflow', 'run', workflow, '--repo', args.repo, '--ref', 'main')
        print('Restarted ' + workflow)


if __name__ == '__main__':
    main()
