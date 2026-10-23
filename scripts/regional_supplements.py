"""Bounded, optional OS Open Names import for one geographic region.

Reads the remote ZIP central directory and selected 10 km grid members using
HTTPS byte ranges. It never falls back to downloading the GB archive. Input
records are filtered to the requested WGS84 bounding box before being written
in the existing Open Names CSV/header layout used by places/search builders.
"""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

API = 'https://api.os.uk/downloads/v1/products/OpenNames/downloads?area=GB&format=CSV'
ATTRIBUTION = ('Contains OS data © Crown copyright and database rights; Contains Royal Mail data '
               '© Royal Mail copyright and database right; Contains National Statistics data '
               '© Crown copyright and database right')
ALLOWED_HOSTS = {'api.os.uk', 'downloads.os.uk', 'osdatahubdownloads.blob.core.windows.net'}
MAX_TRANSFER = 32 * 1024 * 1024
MAX_MEMBER = 12 * 1024 * 1024
MAX_MEMBERS = 64
DEFAULT_COLS = {'NAME1': 2, 'LOCAL_TYPE': 7, 'GEOMETRY_X': 8, 'GEOMETRY_Y': 9}


class SourceUnavailable(ValueError):
    """A source cannot safely supply the requested region."""


def bounds_checked(bounds):
    if len(bounds) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds):
        raise ValueError('Four finite geographic bounds required')
    w, s, e, n = bounds
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError('Invalid geographic bounds')
    # Avoid an accidental nationwide CSV import from the regional build path.
    if e - w > 3 or n - s > 3:
        raise ValueError('Subdivide large supplementary imports into smaller regions')
    return list(bounds)


def approved_url(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in ALLOWED_HOSTS or p.username or p.password
            or p.port not in (None, 443) or p.fragment):
        raise SourceUnavailable('Unapproved OS download location')
    return url


class CheckedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        approved_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url, start=None, end=None):
    headers = {'User-Agent': 'DriveMate-regional-map/1', 'Accept-Encoding': 'identity'}
    if start is not None:
        headers['Range'] = f'bytes={start}-{end}'
    request = Request(approved_url(url), headers=headers)
    with build_opener(CheckedRedirects()).open(request, timeout=45) as response:
        limit = 1024 * 1024 if start is None else end - start + 1
        data = response.read(limit + 1)
        if len(data) > limit:
            raise SourceUnavailable('OS server did not honour the bounded request')
        return response.status, dict(response.headers.items()), data, response.geturl()


class HTTPRangeReader(io.RawIOBase):
    """Seekable ZIP input, with a shared cap on the actual bytes downloaded."""
    def __init__(self, url, size, fetch=http_get, budget=MAX_TRANSFER):
        if type(size) is not int or not 22 <= size <= 2_000_000_000:
            raise SourceUnavailable('Invalid declared archive size')
        self.url, self.size, self.fetch = approved_url(url), size, fetch
        self.position, self.transferred, self.budget = 0, 0, budget
        self.final_url = self.url
        self.requests = 0
        self.deadline = time.monotonic() + 180

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        target = offset if whence == io.SEEK_SET else self.position + offset if whence == io.SEEK_CUR else self.size + offset if whence == io.SEEK_END else -1
        if not 0 <= target <= self.size:
            raise SourceUnavailable('Invalid archive seek')
        self.position = target
        return target

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.size - self.position
        size = min(size, self.size - self.position)
        if size == 0:
            return b''
        if self.requests >= 200 or time.monotonic() > self.deadline:
            raise SourceUnavailable('Regional OS request/time budget exceeded')
        if size > MAX_MEMBER or self.transferred + size > self.budget:
            raise SourceUnavailable('Regional OS transfer budget exceeded')
        start, end = self.position, self.position + size - 1
        self.requests += 1
        status, headers, data, final = self.fetch(self.url, start, end)
        headers = {k.lower(): v for k, v in headers.items()}
        if (status != 206 or headers.get('content-range') != f'bytes {start}-{end}/{self.size}'
                or len(data) != size or headers.get('content-encoding', 'identity') != 'identity'):
            raise SourceUnavailable('OS archive server must support exact byte ranges (status ' + str(int(status)) + ')')
        approved_url(final)
        self.final_url = final
        self.position += size
        self.transferred += len(data)
        return data


def grid_code(easting, northing):
    """OS National Grid 10 km sheet name, using coordinates in EPSG:27700."""
    e, n = int(math.floor(easting / 100000)), int(math.floor(northing / 100000))
    if not (0 <= e <= 6 and 0 <= n <= 12):
        return None
    first = (19 - n) - (19 - n) % 5 + (e + 10) // 5
    second = ((19 - n) * 5) % 25 + e % 5
    if first > 7:
        first += 1
    if second > 7:
        second += 1
    return chr(65 + first) + chr(65 + second) + str(int(easting % 100000 // 10000)) + str(int(northing % 100000 // 10000))


def grid_sheets(bounds):
    from pyproj import Transformer
    w, s, e, n = bounds_checked(bounds)
    transform = Transformer.from_crs('EPSG:4326', 'EPSG:27700', always_xy=True)
    # Sample all edges, not only corners: projected bounding edges can curve.
    points = [transform.transform(w + (e - w) * k / 20, y) for k in range(21) for y in (s, n)]
    points += [transform.transform(x, s + (n - s) * k / 20) for k in range(21) for x in (w, e)]
    if any(not math.isfinite(v) for p in points for v in p):
        raise SourceUnavailable('Region is outside the National Grid')
    west, east = min(p[0] for p in points), max(p[0] for p in points)
    south, north = min(p[1] for p in points), max(p[1] for p in points)
    sheets = set()
    for x in range(math.floor(west / 10000), math.floor(east / 10000) + 1):
        for y in range(math.floor(south / 10000), math.floor(north / 10000) + 1):
            code = grid_code(x * 10000, y * 10000)
            if code:
                sheets.add(code)
    if not sheets or len(sheets) > MAX_MEMBERS:
        raise SourceUnavailable('Regional OS sheet selection exceeds its bounds')
    return sheets


def selected_members(archive, sheets):
    names, header = [], None
    if len(archive.infolist()) > 20000:
        raise SourceUnavailable('Oversized OS archive inventory')
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or '..' in path.parts or '\\' in info.filename:
            raise SourceUnavailable('Unsafe archive member path')
        if path.suffix.lower() != '.csv':
            continue
        if 'header' in path.name.lower():
            if header is not None:
                raise SourceUnavailable('Ambiguous Open Names header')
            header = info
        elif path.stem.upper() in sheets:
            names.append(info)
    if not names or len(names) > MAX_MEMBERS or header is None:
        raise SourceUnavailable('Expected regional OS CSV sheets/header not found')
    if len({PurePosixPath(i.filename).stem.upper() for i in names}) != len(names):
        raise SourceUnavailable('Duplicate OS sheet names')
    for info in [header, *names]:
        if info.file_size > MAX_MEMBER or info.compress_size > MAX_MEMBER or info.flag_bits & 1:
            raise SourceUnavailable('Oversized or encrypted regional OS member')
    return header, sorted(names, key=lambda x: x.filename)


def extract_region(reader, bounds, output_dir, sheets=None):
    from pyproj import Transformer
    bounds = bounds_checked(bounds)
    directory = Path(output_dir)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('OS regional output must be empty')
    directory.mkdir(parents=True, exist_ok=True)
    to_wgs = Transformer.from_crs('EPSG:27700', 'EPSG:4326', always_xy=True)
    count, paths = 0, []
    with zipfile.ZipFile(reader) as archive:
        header, members = selected_members(archive, sheets or grid_sheets(bounds))
        raw = archive.read(header)
        fields = next(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
        columns = {key.strip(): index for index, key in enumerate(fields)}
        if not set(DEFAULT_COLS) <= columns.keys():
            raise SourceUnavailable('Unsupported Open Names CSV columns')
        (directory / 'OS_Open_Names_Header.csv').write_bytes(raw)
        for info in members:
            kept = []
            text = archive.read(info).decode('utf-8-sig')
            for row in csv.reader(io.StringIO(text)):
                if len(row) < len(fields):
                    raise SourceUnavailable('Truncated Open Names CSV record')
                try:
                    lon, lat = to_wgs.transform(float(row[columns['GEOMETRY_X']]), float(row[columns['GEOMETRY_Y']]))
                except (ValueError, OverflowError):
                    raise SourceUnavailable('Invalid Open Names coordinates') from None
                if not math.isfinite(lon) or not math.isfinite(lat):
                    raise SourceUnavailable('Invalid projected Open Names coordinates')
                if bounds[0] <= lon <= bounds[2] and bounds[1] <= lat <= bounds[3]:
                    kept.append(row)
            path = directory / (PurePosixPath(info.filename).stem.upper() + '.csv')
            with path.open('w', encoding='utf-8', newline='') as stream:
                csv.writer(stream, lineterminator='\n').writerows(kept)
            count += len(kept)
            paths.append({'file': path.name, 'records': len(kept), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    if count == 0:
        raise SourceUnavailable('No OS records found in requested region')
    return {'directory': str(directory), 'records': count, 'files': paths,
            'downloaded_bytes': getattr(reader, 'transferred', None)}


def import_os_open_names(bounds, output_dir, *, distribution_url=None, fetch=http_get):
    """Return explicit source status; never convert failed access into checked-empty data."""
    bounds = bounds_checked(bounds)
    result = {'id': 'os-open-names', 'status': 'unavailable', 'bounds': bounds,
              'origin': API, 'licence': 'OGL-v3; third-party credits apply',
              'terms': 'https://osdatahub.os.uk/downloads/open/OpenNames', 'attribution': ATTRIBUTION}
    directory = Path(output_dir)
    if directory.is_symlink() or (directory.exists() and any(directory.iterdir())):
        result['reason'] = 'OutputNotEmptyOrUnsafe'
        return result
    stage = 'catalogue'
    reader = None
    try:
        status, _, raw, _ = fetch(API)
        listings = json.loads(raw)
        if status != 200 or not isinstance(listings, list):
            raise SourceUnavailable('Invalid public OS download catalogue')
        candidates = [item for item in listings if item.get('area') == 'GB' and item.get('format') == 'CSV']
        if len(candidates) != 1:
            raise SourceUnavailable('Ambiguous OS CSV distribution')
        item = candidates[0]
        stage = 'regional-archive'
        reader = HTTPRangeReader(distribution_url or item['url'], item['size'], fetch)
        extracted = extract_region(reader, bounds, output_dir)
        result.update(extracted)
        result.update(status='imported', archive_md5=item.get('md5'), archive_bytes=item['size'],
                      distribution_file=item.get('fileName'), scope='Only selected grid CSV rows inside bounds')
    except Exception as error:
        # Remote bodies and signed redirect query parameters must not enter public logs.
        result['reason'] = type(error).__name__
        result['stage'] = stage
        if isinstance(error, SourceUnavailable):
            # SourceUnavailable messages are controlled literals raised here;
            # never include generic exception text (URLs, bodies or tokens).
            result['detail'] = str(error)[:180]
        elif isinstance(error, HTTPError):
            result['detail'] = 'OS HTTPS request returned status ' + str(int(error.code))
        elif isinstance(error, zipfile.BadZipFile):
            result['detail'] = 'Remote archive is not a supported ZIP'
        else:
            result['detail'] = 'OS import failed at the recorded stage; remote error text withheld'
        if reader is not None:
            result['downloaded_bytes'] = reader.transferred
            result['range_requests'] = reader.requests
        directory = Path(output_dir)
        if directory.exists():
            for child in directory.iterdir():
                if child.is_file() and not child.is_symlink():
                    child.unlink()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bounds', required=True, help='west,south,east,north')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = import_os_open_names([float(v) for v in args.bounds.split(',')], args.out)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
