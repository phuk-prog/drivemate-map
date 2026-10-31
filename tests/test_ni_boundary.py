import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ni_boundary  # noqa: E402

NI_SQUARE = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="test">
  <node id="1" lat="54.0" lon="-7.0" version="1"/>
  <node id="2" lat="54.0" lon="-6.0" version="1"/>
  <node id="3" lat="55.0" lon="-6.0" version="1"/>
  <node id="4" lat="55.0" lon="-7.0" version="1"/>
  <node id="5" lat="50.0" lon="0.0" version="1"/>
  <node id="6" lat="50.0" lon="1.0" version="1"/>
  <node id="7" lat="51.0" lon="1.0" version="1"/>
  <way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/></way>
  <way id="11" version="1"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="5"/></way>
  <relation id="100" version="1">
    <member type="way" ref="10" role="outer"/>
    <tag k="type" v="boundary"/>
    <tag k="boundary" v="administrative"/>
    <tag k="admin_level" v="4"/>
    <tag k="name" v="Northern Ireland"/>
  </relation>
  <relation id="101" version="1">
    <member type="way" ref="11" role="outer"/>
    <tag k="type" v="boundary"/>
    <tag k="boundary" v="administrative"/>
    <tag k="admin_level" v="4"/>
    <tag k="name" v="England"/>
  </relation>
</osm>
"""


def write_osm(directory, text):
    path = Path(directory) / 'extract.osm'
    path.write_text(text, encoding='utf-8')
    return path


class NorthernIrelandBoundaryTest(unittest.TestCase):
    def test_finds_only_the_northern_ireland_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            geom = ni_boundary.boundary_geometry(write_osm(tmp, NI_SQUARE))
        self.assertEqual((-7.0, 54.0, -6.0, 55.0), tuple(round(v, 6) for v in geom.bounds))
        self.assertTrue(geom.is_valid)

    def test_no_boundary_fails_closed(self):
        text = NI_SQUARE.replace('<tag k="name" v="Northern Ireland"/>', '<tag k="name" v="Wales"/>')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'found 0'):
                ni_boundary.boundary_geometry(write_osm(tmp, text))

    def test_duplicate_boundary_fails_closed(self):
        extra = NI_SQUARE.replace('id="101"', 'id="102"').replace(
            '<tag k="name" v="England"/>', '<tag k="name" v="Northern Ireland"/>')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'found 2'):
                ni_boundary.boundary_geometry(write_osm(tmp, extra))

    def test_boundary_outside_envelope_fails_closed(self):
        far = (NI_SQUARE
               .replace('<node id="1" lat="54.0" lon="-7.0" version="1"/>', '<node id="1" lat="10.0" lon="-7.0" version="1"/>')
               .replace('<node id="2" lat="54.0" lon="-6.0" version="1"/>', '<node id="2" lat="10.0" lon="-6.0" version="1"/>')
               .replace('<node id="3" lat="55.0" lon="-6.0" version="1"/>', '<node id="3" lat="11.0" lon="-6.0" version="1"/>')
               .replace('<node id="4" lat="55.0" lon="-7.0" version="1"/>', '<node id="4" lat="11.0" lon="-7.0" version="1"/>'))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'envelope'):
                ni_boundary.boundary_geometry(write_osm(tmp, far))

    def test_poly_file_round_trips_the_outer_ring(self):
        with tempfile.TemporaryDirectory() as tmp:
            geom = ni_boundary.boundary_geometry(write_osm(tmp, NI_SQUARE))
            poly = ni_boundary.write_poly(geom, Path(tmp) / 'ni.poly')
            sections = ni_boundary.read_poly_points(poly)
        self.assertEqual(1, len(sections))
        self.assertEqual(4, len(sections[0]))
        self.assertIn((-7.0, 54.0), sections[0])
        self.assertIn((-6.0, 55.0), sections[0])

    def test_holes_are_written_as_exclusive_sections(self):
        from shapely.geometry import Polygon
        outer = Polygon([(-7, 54), (-6, 54), (-6, 55), (-7, 55)], holes=[[(-6.8, 54.2), (-6.6, 54.2), (-6.6, 54.4)]])
        with tempfile.TemporaryDirectory() as tmp:
            poly = ni_boundary.write_poly(outer, Path(tmp) / 'holes.poly')
            text = poly.read_text(encoding='ascii')
            sections = ni_boundary.read_poly_points(poly)
        self.assertIn('!2', text)
        self.assertEqual(2, len(sections))


if __name__ == '__main__':
    unittest.main()
