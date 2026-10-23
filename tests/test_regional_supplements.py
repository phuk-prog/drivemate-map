import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import regional_supplements as regional


class RegionalSupplementTests(unittest.TestCase):
    bounds = [-2.3, 53.3, -1.9, 53.5]
    url = 'https://api.os.uk/downloads/example.zip'

    def archive(self):
        from pyproj import Transformer
        project = Transformer.from_crs('EPSG:4326', 'EPSG:27700', always_xy=True)
        def row(name, lon, lat):
            e, n = project.transform(lon, lat)
            return f'{name},Town,{e},{n}\n'
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('Doc/OS_Open_Names_Header.csv', 'NAME1,LOCAL_TYPE,GEOMETRY_X,GEOMETRY_Y\n')
            z.writestr('Data/SJ/SJ89.csv', row('Stockport', -2.15, 53.41) + row('Outside', 0, 51))
            z.writestr('Data/TQ/TQ38.csv', 'not downloaded\n' * 1000)
        return stream.getvalue()

    def transport(self, data, status=206):
        calls = []
        def fetch(url, start=None, end=None):
            calls.append((url, start, end))
            if start is None:
                return 200, {}, json.dumps([{'url': self.url, 'size': len(data), 'area': 'GB', 'format': 'CSV'}]).encode(), url
            return status, {'Content-Range': f'bytes {start}-{end}/{len(data)}'}, data[start:end+1], url
        return fetch, calls

    def test_grid_sheet_and_stockport_selection(self):
        self.assertEqual('SJ89', regional.grid_code(389000, 393000))
        self.assertEqual('SD34', regional.grid_code(334000, 444000))
        self.assertIn('SJ89', regional.grid_sheets(self.bounds))
        self.assertNotIn('TQ38', regional.grid_sheets(self.bounds))

    def test_range_reader_never_accepts_full_archive_fallback(self):
        data = self.archive()
        fetch, _ = self.transport(data, status=200)
        with self.assertRaises(regional.SourceUnavailable):
            regional.HTTPRangeReader(self.url, len(data), fetch).read(22)

    def test_exact_ranges_and_budget_enforced(self):
        data = self.archive()
        fetch, calls = self.transport(data)
        reader = regional.HTTPRangeReader(self.url, len(data), fetch, budget=30)
        reader.seek(-22, io.SEEK_END)
        self.assertEqual(data[-22:], reader.read(22))
        reader.seek(0)
        with self.assertRaises(regional.SourceUnavailable):
            reader.read(9)
        self.assertEqual(1, len(calls))

    def test_only_region_members_and_rows_are_extracted(self):
        data = self.archive()
        fetch, calls = self.transport(data)
        reader = regional.HTTPRangeReader(self.url, len(data), fetch)
        with tempfile.TemporaryDirectory() as tmp:
            result = regional.extract_region(reader, self.bounds, tmp, sheets={'SJ89'})
            self.assertEqual(1, result['records'])
            self.assertEqual({'SJ89.csv', 'OS_Open_Names_Header.csv'}, {p.name for p in Path(tmp).iterdir()})
            self.assertIn('Stockport', (Path(tmp) / 'SJ89.csv').read_text())
            self.assertNotIn('Outside', (Path(tmp) / 'SJ89.csv').read_text())
        self.assertTrue(all(start is not None for _, start, end in calls))

    def test_public_api_import_reports_source_and_region(self):
        data = self.archive()
        fetch, _ = self.transport(data)
        with tempfile.TemporaryDirectory() as tmp:
            result = regional.import_os_open_names(self.bounds, tmp, fetch=fetch)
            self.assertEqual('imported', result['status'])
            self.assertEqual(1, result['records'])
            self.assertIn('Crown copyright', result['attribution'])

    def test_failure_is_explicit_and_partial_files_are_removed(self):
        data = self.archive()
        fetch, _ = self.transport(data, status=200)
        with tempfile.TemporaryDirectory() as tmp:
            result = regional.import_os_open_names(self.bounds, tmp, fetch=fetch)
            self.assertEqual('unavailable', result['status'])
            self.assertNotIn('records', result)
            self.assertEqual([], list(Path(tmp).iterdir()))

    def test_existing_output_is_preserved_on_rejected_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / 'saved.csv'
            original.write_text('existing usable data')
            value = regional.import_os_open_names(self.bounds, tmp)
            self.assertEqual('unavailable', value['status'])
            self.assertEqual('existing usable data', original.read_text())

    def test_unsafe_archive_paths_are_rejected(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as z:
            z.writestr('../SJ89.csv', 'bad')
        raw.seek(0)
        with zipfile.ZipFile(raw) as archive, self.assertRaises(regional.SourceUnavailable):
            regional.selected_members(archive, {'SJ89'})

    def test_arbitrary_host_or_large_region_rejected(self):
        for url in ('http://api.os.uk/example', 'https://example.com/test', 'https://api.os.uk@evil.example/file'):
            with self.assertRaises(regional.SourceUnavailable):
                regional.approved_url(url)
        with self.assertRaises(ValueError):
            regional.bounds_checked([-8, 50, 2, 59])
