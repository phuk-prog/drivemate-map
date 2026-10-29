"""Bind the existing notice bundle to new DMN indices; packaging, not legal clearance.

Legacy indices remain readable by consumers and rollback checks. Only newly built
releases use this promotion gate. No source identity is invented or inferred.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

FILES = ('NOTICE.md', 'LICENSE-ODbL.txt', 'LICENSE-CDLA-Permissive-2.0.txt',
         'LICENSE-Apache-2.0.txt', 'NOTICE-Foursquare.txt')


def prepare(directory, licenses):
    directory, licenses = Path(directory), Path(licenses)
    index = directory / 'dmn-uk.json'
    doc = json.loads(index.read_text())
    tag = doc['release']
    if not re.fullmatch(r'dmn-uk-\d{8}-\d+', tag):
        raise ValueError('Rights bundle requires a dated DMN release')
    # Read and validate everything before changing the index or copying assets.
    bundle = {}
    for name in FILES:
        path = licenses / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('Missing DMN licence/notice: ' + name)
        bundle[name] = path.read_bytes()
    doc['rights'] = {name: {'tag': tag, 'sha256': hashlib.sha256(data).hexdigest(),
                             'bytes': len(data)} for name, data in bundle.items()}
    for name, data in bundle.items():
        (directory / name).write_bytes(data)
    index.write_text(json.dumps(doc, indent=1, sort_keys=True) + '\n')


def verify(doc, assets):
    rights = doc.get('rights')
    if not isinstance(rights, dict) or set(rights) != set(FILES):
        raise ValueError('Incomplete DMN rights references')
    for name in FILES:
        ref = rights[name]
        matches = [a for a in assets if a.get('name') == name]
        if (not isinstance(ref, dict) or ref.get('tag') != doc.get('release')
                or not isinstance(ref.get('sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', ref['sha256'])
                or type(ref.get('bytes')) is not int or ref['bytes'] <= 0
                or len(matches) != 1 or matches[0].get('size') != ref['bytes']
                or matches[0].get('digest') != 'sha256:' + ref['sha256']):
            raise ValueError('DMN rights asset not verified: ' + name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('directory', type=Path)
    p.add_argument('--licenses', type=Path, default=Path(__file__).resolve().parents[1] / 'licenses')
    p = commands.add_parser('verify')
    p.add_argument('index', type=Path)
    p.add_argument('assets', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.directory, args.licenses)
    else:
        verify(json.loads(args.index.read_text()), json.loads(args.assets.read_text()))
