"""OpenStreetMap hourly changes up to the current 01:00 / 14:00 UK update slot.

Geofabrik publishes each extract about once a day, so on its own a 14:00 run
would find nothing new. Each update slot therefore gathers the official
planet hourly diffs for the WINDOW before the slot ONCE, merges them into one
change file, and keeps it on the map-data-sources release. Every region in
that update applies the same file to its Geofabrik extract before clipping;
osmium keeps the newest version of each object, so overlapping the extract's
own date is harmless. One slot, one file: the chain of regional runs agrees
on exactly which edits are included.
"""
from datetime import timedelta, timezone
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import urlopen

SERVER = 'https://planet.osm.org/replication/hour/'
SOURCES_TAG = 'map-data-sources'
WINDOW = timedelta(hours=30)
MAX_DIFF_BYTES = 200_000_000


def asset_name(slot):
    return 'osm-changes-' + slot.astimezone(timezone.utc).strftime('%Y%m%dT%H') + 'Z.osc.gz'


def fetch(url, target, *, attempts=6, opener=None, sleeper=None):
    """Download with bounded retries for transient failures; partial files never survive."""
    opener = opener or urlopen
    sleeper = sleeper or time.sleep
    target = Path(target)
    for attempt in range(attempts):
        try:
            with opener(url, timeout=60) as response, target.open('wb') as out:
                total = 0
                for block in iter(lambda: response.read(1024 * 1024), b''):
                    total += len(block)
                    if total > MAX_DIFF_BYTES:
                        raise ValueError('Replication diff too large: ' + url)
                    out.write(block)
            return target
        except (OSError, TimeoutError) as exc:
            target.unlink(missing_ok=True)
            if isinstance(exc, HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == attempts - 1:
                raise RuntimeError('Replication diff unavailable after retries: ' + url) from exc
            sleeper(min(2 ** attempt * 5, 60))


def diff_path(seq):
    s = f'{seq:09d}'
    return f'{s[0:3]}/{s[3:6]}/{s[6:9]}.osc.gz'


def build(slot, directory, *, server=None, fetcher=fetch, run=subprocess.run):
    """Merge the hourly diffs from slot-WINDOW to slot into one simplified change file."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if server is None:
        from osmium.replication.server import ReplicationServer
        server = ReplicationServer(SERVER)
    first = server.timestamp_to_sequence(slot - WINDOW)
    last = server.timestamp_to_sequence(slot)
    if first is None or last is None or not 0 < last - first <= 48:
        raise ValueError('Replication window not found on the server')
    # timestamp_to_sequence gives the last diff at or before the time.
    parts = []
    for seq in range(first + 1, last + 1):
        part = directory / f'{seq}.osc.gz'
        fetcher(SERVER + diff_path(seq), part)
        parts.append(part)
    merged = directory / asset_name(slot)
    run(['osmium', 'merge-changes', '--simplify', *map(str, parts), '-o', str(merged), '--overwrite'], check=True)
    for part in parts:
        part.unlink()
    return merged, {'first_sequence': first + 1, 'last_sequence': last}


def ensure(github, slot, directory, *, builder=build):
    """The slot's change file: reuse the copy on GitHub, else build and keep it there."""
    name = asset_name(slot)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    github.ensure_release(SOURCES_TAG)
    if name in github.inventory(SOURCES_TAG):
        return github.download(SOURCES_TAG, name, directory)
    merged, _ = builder(slot, directory)
    try:
        github.upload_many(SOURCES_TAG, [merged])
    except Exception as exc:  # another run may have uploaded the same slot first
        if name not in github.inventory(SOURCES_TAG):
            raise
        print('Change file already kept by another run: ' + str(exc).splitlines()[0], flush=True)
    return merged


def apply(source, changes, out, run=subprocess.run):
    """Apply [changes] to [source]; the newest version of every object is kept."""
    run(['osmium', 'apply-changes', str(source), str(changes), '-o', str(out), '--overwrite'], check=True)
    return Path(out)

