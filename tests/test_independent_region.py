import gzip
import json
from pathlib import Path
import sys
import subprocess
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

    def test_mixed_road_and_poi_exports_feed_real_places_merger_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'all.geojsonseq';selected=root/'places.geojsonseq'
            records=[{'type':'Feature','geometry':{'type':'LineString','coordinates':[[-2.15,53.4],[-2.14,53.4]]},
                      'properties':{'name':'Synthetic Road','highway':'residential'}},
                     {'type':'Feature','geometry':{'type':'Point','coordinates':[-2.15,53.4]},
                      'properties':{'name':'Synthetic public cafe','amenity':'cafe'}}]
            source.write_text(''.join('\x1e'+json.dumps(r)+'\n' for r in records))
            self.assertEqual(1,builder.write_places_input(source,selected))
            self.assertEqual(2,len(list(builder.geojson_features(source))))
            result=subprocess.run([sys.executable,str(builder.ROOT/'scripts/places.py'),str(root/'out'),
                '--osm',str(selected)],capture_output=True,text=True)
            self.assertEqual(0,result.returncode,result.stderr)
            tiles=list((root/'out').glob('places-*.json.gz'))
            self.assertEqual(1,len(tiles))
            self.assertIn('Synthetic public cafe',gzip.decompress(tiles[0].read_bytes()).decode())

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
