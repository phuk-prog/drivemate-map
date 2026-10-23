import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import build_independent_region as builder


class IndependentRegionTests(unittest.TestCase):
    def test_real_geojson_record_separators_are_not_empty_records(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'public-synthetic.geojsonseq'
            feature={'type':'Feature','geometry':{'type':'Point','coordinates':[-2.15,53.4]},
                     'properties':{'name':'Synthetic public place','addr:postcode':'ZZ1 1ZZ'}}
            source.write_text('\x1e'+json.dumps(feature)+'\n\n\x1e'+json.dumps(feature)+'\n')
            self.assertEqual(2,len(list(builder.geojson_features(source))))
            out=Path(directory)/'search.gz'
            self.assertEqual(2,builder.osm_search(source,out,[-2.29,53.30,-1.98,53.47]))
            text=gzip.decompress(out.read_bytes()).decode()
            self.assertIn('synthetic public place',text)
            self.assertIn('zz11zz\tP',text)

    def test_fallback_search_excludes_locations_outside_region(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'synthetic.geojsonseq'
            source.write_text(json.dumps({'type':'Feature','geometry':{'type':'Point','coordinates':[0,0]},
                'properties':{'name':'Outside synthetic point'}})+'\n')
            self.assertEqual(0,builder.osm_search(source,Path(directory)/'search.gz',[-2.29,53.30,-1.98,53.47]))

    def test_malformed_record_is_not_silently_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'bad.geojsonseq';source.write_text('\x1e{"not":"a feature"}\n')
            with self.assertRaises(ValueError):list(builder.geojson_features(source))
