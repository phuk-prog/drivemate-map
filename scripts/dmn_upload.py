"""Upload DMN road pieces to their dated release, surviving GitHub's API rate limit.

2.3: run #41163393325 (31 Oct 2026) built and checked every piece, then failed with
HTTP 403 "API rate limit exceeded" part-way through `gh release upload`, leaving 192 of
772 assets. Phones were safe (the switch step was skipped), but the pack never shipped.

This replaces the one-shot upload with the same recovery the UK map publisher uses:
- create the dated release idempotently, retrying a rate-limited create;
- upload only pieces not already there, in batches;
- on a rate limit, wait for the allowance to reset (bounded), then re-read the release,
  delete any half-finished asset and resume;
- never replace an asset that is already uploaded, and refuse unexpected assets.
The existing "Check every piece uploaded whole" step still verifies sizes before phones switch.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

_spec = importlib.util.spec_from_file_location('publish_map_data', Path(__file__).with_name('publish_map_data.py'))
publisher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publisher)

DATED = re.compile(r'dmn-uk-[0-9]{8}-[0-9]{1,10}')
PIECE = re.compile(r'dmn-[A-Za-z0-9_-]+\.dmnpack')
NOTES = 'DMN road map pieces for the DriveMate app, from OpenStreetMap (© OpenStreetMap contributors, ODbL 1.0).'


def checked_release(tag):
    if not isinstance(tag, str) or not DATED.fullmatch(tag):
        raise ValueError('Invalid dated DMN release tag')
    return tag


class DmnGitHub(publisher.GitHub):
    """The UK publisher's client, limited to dated DMN releases (never the live `dmn-uk` pointer)."""

    def release(self, tag):
        return json.loads(self.run('api', f'repos/{self.repo}/releases/tags/{checked_release(tag)}'))

    def ensure_release(self, tag):
        try:
            return self.release(tag)
        except publisher.GitHubError as exc:
            if 'HTTP 404' not in str(exc) and 'not found' not in str(exc).lower():
                raise
        self.run('release', 'create', checked_release(tag), '--repo', self.repo,
                 '--title', f'DMN road pieces (UK) {tag}', '--notes', NOTES, '--latest=false')
        return self.release(tag)

    def delete(self, asset):
        asset_id = asset.get('id')
        if type(asset_id) is not int or asset_id <= 0:
            raise ValueError('Invalid asset ID')
        self.run('api', '-X', 'DELETE', f'repos/{self.repo}/releases/assets/{asset_id}')


def _ensure(github, tag, waits):
    for attempt in range(waits + 1):
        try:
            github.ensure_release(tag)
            return
        except publisher.RateLimited:
            if attempt == waits:
                raise
            github.wait_for_rate_limit()


def _settle(github, tag, files):
    """Current uploaded assets; half-finished ones are deleted so they are uploaded again."""
    existing = github.inventory(tag)
    unexpected = sorted(set(existing) - set(files))
    if unexpected:
        raise ValueError(f'Dated release contains unexpected asset: {unexpected[0]}')
    complete = {}
    for name, asset in existing.items():
        if asset.get('state', 'uploaded') != 'uploaded':
            github.delete(asset)
        elif asset.get('size') != files[name].stat().st_size:
            raise ValueError(f'Uploaded asset differs from the built piece: {name}')
        else:
            complete[name] = asset
    return complete


def upload(github, tag, paths, batch=publisher.UPLOAD_BATCH, waits=publisher.MAX_RATE_LIMIT_WAITS):
    checked_release(tag)
    files = {}
    for path in map(Path, paths):
        if not PIECE.fullmatch(path.name) or not path.is_file() or path.name in files:
            raise ValueError(f'Invalid DMN piece: {path}')
        files[path.name] = path
    if not files or len(files) > publisher.ASSET_LIMIT:
        raise ValueError('Invalid number of DMN pieces')
    _ensure(github, tag, waits)
    used = 0
    while True:
        try:
            done = _settle(github, tag, files)
            missing = [files[n] for n in sorted(files) if n not in done]
            for start in range(0, len(missing), batch):
                github.upload_many(tag, missing[start:start + batch])
            break
        except publisher.RateLimited:
            if used >= waits:
                raise
            used += 1
            github.wait_for_rate_limit()
    final = _settle(github, tag, files)
    if set(final) != set(files):
        raise ValueError('Not every DMN piece was uploaded')
    return len(final)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--repo', required=True)
    p.add_argument('--release', required=True)
    p.add_argument('pieces', nargs='+')
    a = p.parse_args(argv)
    count = upload(DmnGitHub(a.repo), a.release, a.pieces)
    print(f'Uploaded {count} DMN pieces to {a.release}')


if __name__ == '__main__':
    sys.exit(main())
