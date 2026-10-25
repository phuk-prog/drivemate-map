"""Test 1.6 (owner, 18 Oct 2026): every licence credit is present and worded as its owner asks.

Each rule is one data source DriveMate uses, the wording its owner publishes (link kept with the
rule), and the phrases that wording needs. A file passes a rule when it holds every phrase (case and
spacing as written, curly quotes and dashes treated as plain). Only these published wordings can
fail a file; anything else is reported, never failed.

    python3 scripts/credits_check.py FILE...            # e.g. licenses/NOTICE.md
    python3 scripts/credits_check.py --app DataCredits.kt   # the app's credits screen (all rules)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RULES = {
    "OpenStreetMap": {
        "rule": "https://osmfoundation.org/wiki/Licence/Attribution_Guidelines",
        "needs": ["OpenStreetMap", "openstreetmap.org/copyright"],
        "any": [["ODbL", "Open Database Licence"]],
    },
    "OS Open Names": {
        "rule": "https://www.ordnancesurvey.co.uk/business-government/products/open-map-names",
        "needs": ["Contains OS data © Crown copyright and database right"],
    },
    "Royal Mail (postcodes)": {
        "rule": "https://www.ons.gov.uk/methodology/geography/licences",
        "needs": ["Contains Royal Mail data © Royal Mail copyright and database right"],
    },
    "National Statistics (postcodes)": {
        "rule": "https://www.ons.gov.uk/methodology/geography/licences",
        "needs": ["Contains National Statistics data © Crown copyright and database right"],
    },
    "Overture places (Meta, Microsoft and others)": {
        "rule": "https://docs.overturemaps.org/attribution/",
        "needs": ["Overture"],
        "any": [["CDLA Permissive 2.0", "CDLA-Permissive-2.0"]],
    },
    "Foursquare places": {
        "rule": "https://docs.overturemaps.org/attribution/",
        "needs": ["Foursquare Labs, Inc."],
        "any": [["Apache 2.0", "Apache-2.0"]],
    },
    "Transport for London": {
        "rule": "https://tfl.gov.uk/corporate/terms-and-conditions/transport-data-service",
        "needs": ["Powered by TfL Open Data"],
    },
}
# Rules for data the public repo's own files describe (the app screen needs every rule)
DATA_RULES = ["OpenStreetMap", "OS Open Names", "Royal Mail (postcodes)", "National Statistics (postcodes)",
              "Overture places (Meta, Microsoft and others)", "Foursquare places", "Transport for London"]


def plain(text: str) -> str:
    """Curly quotes, dashes and runs of spaces or line breaks read as plain text."""
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    text = re.sub(r'"\s*\+\s*"', "", text)  # Kotlin "..." + "..." joins
    return re.sub(r"\s+", " ", text)


def has(body: str, phrase: str) -> bool:
    """The phrase as a whole: "database right" is not found inside "database rights"."""
    return re.search(r"(?<![A-Za-z])" + re.escape(plain(phrase)) + r"(?![A-Za-z])", body) is not None


def missing(text: str, name: str) -> list[str]:
    rule, body = RULES[name], plain(text)
    out = [p for p in rule["needs"] if not has(body, p)]
    out += [" or ".join(group) for group in rule.get("any", []) if not any(has(body, p) for p in group)]
    return out


def check(files: dict[str, str], names: list[str]) -> tuple[list[str], int]:
    lines, fails = [], 0
    for name in names:
        bad = {f: missing(t, name) for f, t in files.items()}
        bad = {f: m for f, m in bad.items() if m}
        fails += len(bad)
        lines.append(f"- {'PASS' if not bad else 'FAIL'} {name} ({RULES[name]['rule']})")
        lines += [f"  - {f} lacks: " + "; ".join(f'"{p}"' for p in m) for f, m in bad.items()]
    return lines, fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--app", help="the app's credits file: must hold every rule")
    a = ap.parse_args()
    files = {f: Path(f).read_text() for f in a.files}
    if a.app:
        files[a.app] = Path(a.app).read_text()
    lines, fails = check(files, DATA_RULES)
    print(f"## Credits (test 1.6): {'pass' if not fails else f'{fails} missing'}\n")
    print("\n".join(lines))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
