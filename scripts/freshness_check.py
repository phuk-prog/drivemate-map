"""Is the published map up to date with OpenStreetMap?

Compares what DriveMate published with what Geofabrik serves now, using only recorded facts:
the published source checkpoint (Geofabrik's MD5 change signal for the extract the build
downloaded, reached through latest.json -> manifest.json, checksums checked), the dated build
tag, the DMN road-piece index, and Geofabrik's current .md5 and Last-Modified date.

  a. Same as Geofabrik now: the checkpoint MD5 equals Geofabrik's current MD5; else Geofabrik's
     newer file must be at most MAX_DAYS old relative to the build (weekly build plus margin).
  b. DMN road pieces built from the same snapshot as the published map (dmn-uk.json "source"
     names latest.json's extra_tag).
  c. The published snapshot is at most MAX_DAYS old.

    python3 scripts/freshness_check.py --latest latest.json --manifest manifest.json \\
        --checkpoint source-checkpoint.json --dmn dmn-uk.json \\
        --geofabrik-md5 geofabrik.md5 --geofabrik-modified "Sun, 18 Oct 2026 20:41:12 GMT"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

MAX_DAYS = 8
TAG = re.compile(r"^map-data-(\d{8})-")
MD5 = re.compile(r"^([0-9a-f]{32})\b")


def build_date(tag: str) -> datetime:
    m = TAG.match(tag or "")
    if not m:
        raise ValueError(f"build tag {tag!r} carries no date")
    return datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)


def check(latest: dict, manifest: dict, checkpoint_bytes: bytes, dmn: dict, geofabrik_md5: str,
          geofabrik_modified: datetime, now: datetime) -> list[tuple[str, bool, str]]:
    """(check, passed, reason) for each of a, b, c."""
    out = []
    info = (manifest.get("files") or {}).get("source-checkpoint.json") or {}
    if info.get("sha256") != hashlib.sha256(checkpoint_bytes).hexdigest():
        raise ValueError("source checkpoint does not match the published manifest")
    checkpoint = json.loads(checkpoint_bytes)
    ours = checkpoint["change_signal"]["value"]
    m = MD5.match(geofabrik_md5.strip())
    if not m:
        raise ValueError("Geofabrik's .md5 could not be read")
    theirs = m.group(1)
    built = build_date(latest.get("tag", ""))
    if ours == theirs:
        out.append(("Same as OpenStreetMap now", True,
                    f"the published map is Geofabrik's current extract ({theirs[:8]}...)"))
    else:
        behind = (geofabrik_modified - built).total_seconds() / 86400
        out.append(("Same as OpenStreetMap now", behind <= MAX_DAYS,
                    f"Geofabrik has a newer extract (from {geofabrik_modified:%d %b %Y}); "
                    f"the published map (built {built:%d %b %Y}) is {max(0.0, behind):.1f} days behind"
                    + ("" if behind <= MAX_DAYS else f", more than {MAX_DAYS}")))
    extra = latest.get("extra_tag", "")
    source = str(dmn.get("source", ""))
    out.append(("DMN road pieces from the same snapshot", bool(extra) and source.endswith(extra),
                f"road pieces say {source!r}; the published map is {extra!r}"))
    age = (now - built).total_seconds() / 86400
    out.append((f"Snapshot at most {MAX_DAYS} days old", age <= MAX_DAYS, f"built {built:%d %b %Y}, {age:.1f} days ago"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    for name in ("latest", "manifest", "checkpoint", "dmn", "geofabrik-md5"):
        ap.add_argument(f"--{name}", required=True, type=Path)
    ap.add_argument("--geofabrik-modified", required=True, help="Geofabrik's Last-Modified header")
    ap.add_argument("--now", help="ISO time (tests); default: now")
    a = ap.parse_args(argv)
    now = datetime.fromisoformat(a.now) if a.now else datetime.now(timezone.utc)
    try:
        rows = check(json.loads(a.latest.read_text()), json.loads(a.manifest.read_text()), a.checkpoint.read_bytes(),
                     json.loads(a.dmn.read_text()), a.geofabrik_md5.read_text(),
                     parsedate_to_datetime(a.geofabrik_modified), now)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"## Map up to date: cannot check\n\n{exc}")
        return 1
    passed = sum(ok for _, ok, _ in rows)
    print(f"## Map up to date: {passed}/{len(rows)} pass\n")
    for name, ok, why in rows:
        print(f"- {'PASS' if ok else 'FAIL'} {name}: {why}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
