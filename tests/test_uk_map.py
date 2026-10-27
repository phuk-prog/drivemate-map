from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import uk_map


class UkMapTests(unittest.TestCase):
    def catalog(self, sha):
        return {'regions': {'wales': {'manifest': {'sha256': sha}}}}

    def test_made_only_when_the_regions_changed(self):
        self.assertTrue(uk_map.due(self.catalog('a'), None))
        self.assertTrue(uk_map.due(self.catalog('b'), {'regions': {'wales': 'a'}}))
        self.assertFalse(uk_map.due(self.catalog('a'), {'regions': {'wales': 'a'}}))
        self.assertFalse(uk_map.due({'regions': {}}, None), 'nothing published to follow')

    def test_too_small_or_too_large_is_never_published(self):
        folder = Path(tempfile.mkdtemp())
        small = folder / 'small.pmtiles'
        small.write_bytes(b'x' * 10)
        with self.assertRaises(ValueError):
            uk_map.check_size(small)
        big = folder / 'big.pmtiles'
        with big.open('wb') as f:
            f.truncate(uk_map.publisher.MAX_ASSET_BYTES + 1)
        with self.assertRaisesRegex(ValueError, 'regional maps'):
            uk_map.check_size(big)

    def test_pointer_names_only_a_uk_map_release(self):
        good = {'schema': 1, 'map': {'tag': 'map-data-ukmap-r5-a1', 'name': 'drivemate-v1.pmtiles',
                                     'layout': 'drivemate-v1', 'sha256': 'a' * 64, 'bytes': 900_000_000}}
        self.assertEqual(good, uk_map.validate_pointer(good))
        for change in ({'tag': 'map-data-region-wales-r1-a1'}, {'layout': 'legacy'}, {'bytes': 5},
                       {'name': 'drivemate.pmtiles'}):
            bad = {**good, 'map': {**good['map'], **change}}
            with self.assertRaises(ValueError, msg=str(change)):
                uk_map.validate_pointer(bad)


if __name__ == '__main__':
    unittest.main()
