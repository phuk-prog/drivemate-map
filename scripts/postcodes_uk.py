"""Every live UK postcode as offline search lines (search-offline-uk.tsv.gz format, type P).

Source: the open postcode file (Open Government Licence v3; ONS data, repackaged by
getthedata.com as open_postcode_geo.csv). Only rows with status "live" are written. Format
follows search_offline.py: key TAB type TAB name TAB area TAB lat TAB lon, sorted by key, with
an empty name for postcodes and the outward code as the area (this file has no town).

Usage: postcodes_uk.py open_postcode_geo.csv.zip out-postcodes-uk.tsv.gz [--date YYYY-MM-DD]
"""
import argparse
import csv
import datetime
import gzip
import io
import sys
import zipfile

CREDIT = ("Contains OS data (c) Crown copyright and database right; Office for National Statistics "
          "postcode data, Open Government Licence v3.0")


def key_of(postcode):
    """Postcode without its space, lower case: "SK8 2EZ" -> "sk82ez"."""
    return "".join(ch for ch in postcode.lower() if ch.isalnum())


def lines_from_csv(text_file):
    """Yield (key, line) for each live postcode row with a usable WGS84 point."""
    for row in csv.reader(text_file):
        if len(row) < 10 or row[1] != "live":
            continue
        postcode = " ".join(row[0].split())
        try:
            lat, lon = float(row[7]), float(row[8])
        except ValueError:
            continue
        if not (49.8 <= lat <= 60.9 and -8.7 <= lon <= 1.8):
            continue
        outward = postcode.split(" ")[0]
        key = key_of(postcode)
        yield key, f"{key}\tP\t\t{outward}\t{lat:.4f}\t{lon:.4f}\n"


def build(zip_path, out_path, date=None):
    date = date or datetime.date.today().isoformat()
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            seen = {}
            for key, line in lines_from_csv(text):
                seen.setdefault(key, line)
    with gzip.open(out_path, "wt", encoding="utf-8", newline="\n") as out:
        out.write(f"#drivemate-search-offline\t1\t{date}\t{CREDIT}\n")
        for key in sorted(seen):
            out.write(seen[key])
    return len(seen)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("zip")
    ap.add_argument("out")
    ap.add_argument("--date")
    args = ap.parse_args(argv)
    count = build(args.zip, args.out, args.date)
    print(f"{count} live postcodes written", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
