"""Northern Ireland clip boundary from the OpenStreetMap extract (category 1.1).

The old clip was a rectangle that included land across the Irish border. This
module reads the 'Northern Ireland' administrative boundary (admin_level 4) that
is already inside the Ireland-and-Northern-Ireland extract, assembles it with
pyosmium, and writes an osmium .poly file for `osmium extract -p`. It fails
closed: no match, several matches, an invalid shape or a shape outside the
expected envelope all raise, and no clipped region is produced.
"""
from pathlib import Path

import osmium
from osmium.geom import WKTFactory
from shapely import wkt
from shapely.geometry import MultiPolygon

NAME = 'Northern Ireland'
# Loose sanity envelope (west, south, east, north). The real region sits inside it;
# a relation outside it is the wrong object.
ENVELOPE = (-8.3, 53.9, -5.3, 55.4)


def boundary_geometry(osm_path):
    """Return the one Northern Ireland boundary in an OSM file as a shapely geometry."""
    matches = []
    for obj in osmium.FileProcessor(str(osm_path)).with_areas().with_locations():
        if (obj.is_area() and obj.tags.get('name') == NAME
                and obj.tags.get('boundary') == 'administrative'
                and obj.tags.get('admin_level') == '4'):
            try:
                matches.append(WKTFactory().create_multipolygon(obj))
            except RuntimeError as exc:
                raise ValueError(f'Northern Ireland boundary could not be assembled: {exc}') from exc
    if len(matches) != 1:
        raise ValueError(f'Expected one Northern Ireland boundary, found {len(matches)}')
    geom = wkt.loads(matches[0])
    if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon', 'MultiPolygon'):
        raise ValueError('Invalid Northern Ireland boundary geometry')
    west, south, east, north = geom.bounds
    if not (ENVELOPE[0] <= west and east <= ENVELOPE[2] and ENVELOPE[1] <= south and north <= ENVELOPE[3]):
        raise ValueError('Northern Ireland boundary lies outside the expected envelope')
    return geom


def write_poly(geom, path, name='northern-ireland'):
    """Write a Polygon or MultiPolygon in osmium .poly format; interiors become holes."""
    polygons = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    lines = [name]
    number = 0
    for polygon in polygons:
        rings = [(polygon.exterior, False)] + [(ring, True) for ring in polygon.interiors]
        for ring, is_hole in rings:
            number += 1
            lines.append(f'!{number}' if is_hole else str(number))
            for lon, lat in list(ring.coords)[:-1]:
                lines.append(f'  {lon:.7f}  {lat:.7f}')
            lines.append('END')
    lines.append('END')
    Path(path).write_text('\n'.join(lines) + '\n', encoding='ascii')
    return Path(path)


def read_poly_points(path):
    """Parse the points of a .poly file back, for checks: one list per section."""
    sections, current = [], None
    for raw in Path(path).read_text(encoding='ascii').splitlines()[1:]:
        line = raw.strip()
        if not line:
            continue
        if line == 'END':
            if current is None:
                break
            sections.append(current)
            current = None
        elif current is None:
            current = []
        else:
            lon, lat = line.split()
            current.append((float(lon), float(lat)))
    return sections
