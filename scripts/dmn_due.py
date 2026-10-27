"""Whether DMN's UK road pieces need rebuilding: any region changed since the live pieces.

The live index (dmn-uk.json on the dmn-uk release) records the regional catalogue it was built
alongside ("catalog_regions": each region's manifest digest). Writes due=true|false for the workflow and
the current identity to <work>/regions.json for the new index.
"""
import argparse
import json
import os
from pathlib import Path
from urllib.request import urlopen

import progressive_regions as regions
import publish_map_data as publisher
import uk_join

INDEX = 'https://github.com/{repo}/releases/download/dmn-uk/dmn-uk.json'


def due(catalog, index):
    current = uk_join.identity(catalog)
    return bool(current) and (not index or index.get('catalog_regions') != current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--work', type=Path, default=Path('work/dmn-due'))
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    catalog = regions.load_catalog(publisher.GitHub(args.repo), args.work / 'catalog')
    try:
        with urlopen(INDEX.format(repo=args.repo), timeout=60) as response:
            index = json.loads(response.read(4_000_000))
    except Exception as exc:
        print(f'No live DMN index ({exc}); building')
        index = None
    result = args.force or due(catalog, index)
    (args.work / 'regions.json').write_text(json.dumps(uk_join.identity(catalog)))
    print('DMN road pieces ' + ('due' if result else 'already built from the current regions'))
    with open(os.environ.get('GITHUB_OUTPUT', os.devnull), 'a') as out:
        out.write(f'due={"true" if result else "false"}\n')


if __name__ == '__main__':
    main()
