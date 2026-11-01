import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import uk_join  # noqa: E402

HEADER = '#drivemate-search-offline\t1\t2026-11-01\tcredit A'


class NationalPostcodeJoinTest(unittest.TestCase):
    def test_national_postcodes_join_without_duplicating_regional_ones(self):
        regional = (HEADER, ['sk82ez\tP\t\tSK8\t53.3700\t-2.2300'])
        national = ('#drivemate-search-offline\t1\t2026-11-01\tcredit B',
                    ['sk82ez\tP\t\tSK8\t53.3701\t-2.2301', 'ab10aa\tP\t\tAB1\t57.1015\t-2.2429'])
        header, rows = uk_join.merge_search([regional, national])
        self.assertIn('ab10aa\tP\t\tAB1\t57.1015\t-2.2429', rows)
        sk = [r for r in rows if r.startswith('sk82ez')]
        self.assertEqual(1, len(sk))
        self.assertIn('credit A', header)
        self.assertIn('credit B', header)

    def test_join_key_changes_when_the_postcode_file_changes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pc.tsv.gz'
            path.write_bytes(b'one')
            uk_join.NATIONAL_POSTCODES = path
            first = uk_join.join_key(uk_join.SEARCH, [])
            path.write_bytes(b'two')
            second = uk_join.join_key(uk_join.SEARCH, [])
            uk_join.NATIONAL_POSTCODES = None
        self.assertNotEqual(first, second)


if __name__ == '__main__':
    unittest.main()
