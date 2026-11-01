import gzip
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import postcodes_uk  # noqa: E402


def make_zip(directory, rows):
    path = Path(directory) / 'open_postcode_geo.csv.zip'
    body = '\n'.join(','.join(r) for r in rows) + '\n'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('open_postcode_geo.csv', body)
    return path


class PostcodeLinesTest(unittest.TestCase):
    def test_live_postcodes_only_sorted_with_keys_and_outward_area(self):
        rows = [
            ['SK8 2EZ', 'live', 'small', '1', '2', '1', 'England', '53.3700', '-2.2300', 'x', 'y', 'z'],
            ['AB1 0AA', 'live', 'small', '1', '2', '1', 'Scotland', '57.1015', '-2.2429', 'x', 'y', 'z'],
            ['ZZ9 9ZZ', 'terminated', 'small', '1', '2', '1', 'England', '53.0', '-2.0', 'x', 'y', 'z'],
            ['BAD 1AA', 'live', 'small', '1', '2', '1', 'England', 'n/a', 'n/a', 'x', 'y', 'z'],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            src = make_zip(tmp, rows)
            out = Path(tmp) / 'out.tsv.gz'
            count = postcodes_uk.build(src, out, date='2026-11-01')
            text = gzip.open(out, 'rt', encoding='utf-8').read().splitlines()
        self.assertEqual(2, count)
        self.assertTrue(text[0].startswith('#drivemate-search-offline\t1\t2026-11-01\t'))
        body = text[1:]
        self.assertEqual(['ab10aa', 'sk82ez'], [line.split('\t')[0] for line in body])
        self.assertEqual('ab10aa\tP\t\tAB1\t57.1015\t-2.2429', body[0])
        self.assertEqual('sk82ez\tP\t\tSK8\t53.3700\t-2.2300', body[1])
        self.assertTrue(all(len(line.split('\t')) == 6 for line in body))


if __name__ == '__main__':
    unittest.main()
