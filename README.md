# DriveMate map data

Open map data for DriveMate, built **region first** by
[regional-maps.yml](.github/workflows/regional-maps.yml). Stockport is published
first, then Manchester, then Greater Manchester, Wales, Scotland and Northern
Ireland, followed by the smallest available English regional extracts ordered
outwards, plus the **Isle of Man** and the **Channel Islands** (Jersey, Guernsey,
Alderney, Sark). A successful publication automatically starts the next regional job.
There is no whole-UK *detailed* archive. Zoomed-out views use the separate
low-detail **UK overview map** (below).

Each region has its own immutable map, source snapshot, mapped lanes/limits,
places/search, warnings, feature/relation packs, signs and regional Valhalla graph.
The verified catalog is published at `map-regions/catalog.json` in Releases.
Large regions are automatically split across multiple immutable GitHub releases
(maximum 900 map assets per release), with every file tagged and hash-verified
before the shared catalog advances. Failed later shards never mark a region ready.
Only regions listed there are available. Coverage remains explicitly **partial**;
completing the extract list alone is not proof that every UK road was verified.

Twice a day, at **01:00 and 14:00 Europe/London** (summer and winter), GitHub
starts an update. Each region's Geofabrik extract is brought up to that time with
the official OpenStreetMap **hourly changes** (`osm_changes.py`): the diffs for the
30 hours before the update time are merged **once** into one change file, kept on
the `map-data-sources` release and applied by every region in that update, so the
14:00 update carries the morning's edits even though Geofabrik publishes once a day.
Each region is then compared using canonical local OSM content; unchanged content
is not rebuilt and identical assets are reused by SHA-256. Overture, OS Open Names
and builder identity changes also trigger a rebuild.

Reliability:
- every download is retried with waits in between;
- the 900 MB sea outlines are kept in the Actions cache and a copy is kept on the
  `map-data-sources` release, used whenever osmdata.openstreetmap.de is slow;
- regions are **built six at a time** (plan → parallel builds → one catalogue change), so
  a full UK update takes about an hour; **one failed region does not stop the others**;
  it stays behind and the hourly `map-watchdog.yml` starts another update for it;
- the watchdog also restarts an update GitHub dropped or delayed, and a stale overview;
- a source whose change signal cannot be read holds back only its own regions;
- if the hourly changes are unavailable the region is built from the extract as
  published and noted (`osm_changes: unavailable` in the catalog).

A failed check or upload never results in publication. GitHub scheduling is best
effort and may start later than the target time.

## UK overview map

[uk-overview.yml](.github/workflows/uk-overview.yml) builds one low-detail map of
the whole UK, Isle of Man and Channel Islands, zoom 0 to 10, at the same 01:00 and
14:00 times: sea, lakes, woods, parks, motorways, A and B roads, ferries and towns
(`uk_overview.py`). It is checked for roads near eight towns across all nations and
both island groups, uploaded to its own immutable `map-data-overview-r…` tag, and only
then the pointer `map-data-overview/overview.json` moves to it. The app draws it when
zoomed out and the regional detail when zoomed in. It never touches the regional catalog.

The old `map-data.yml` whole-UK-first workflow is disabled. Its scripts and tests
remain as historical format/compatibility references; it is not the active build.

The tables below document recoverable formats. The regional `source-inventory.json`
reports which sources were actually imported; availability is never inferred from
this table. Legacy filenames ending `-uk` inside a regional manifest are still
**region-scoped**, not claims of UK-wide contents.

| File | What it is |
|---|---|
| `feature-packs-<row>_<col>.jsonl.gz`, global pack and `feature-packs-manifest.json` | Original exported OSM feature IDs, all tags and geographic geometry, including unnamed signals, entrances, kerbs and markings where mapped. Regional gzip NDJSON supports streaming readers; large features are retained in the global pack. New app integration is required to display/query them |
| `relation-packs-<bucket>.jsonl.gz` and `relation-packs-manifest.json` | OSM relations with all tags and ordered typed member references/roles, including mapped lane connections and turn restrictions. Index bucket is relation ID divided by 100,000; these are source facts, not inferred lane paths |
| `osm-source-manifest.json` and `osm-source.partNNNNN` | Exact complete supplied OSM PBF, split into checked source archive chunks. Retains objects geographic export cannot represent; not a default phone download |
| `source-checkpoint.json` | Verified source identity and builder fingerprint. Daily checks compare the Geofabrik change signal; only successfully published generations advance the checkpoint |
| `drivemate.pmtiles` | Map pictures for the UK (the app streams just the parts it needs) |
| `lanes-<row>_<col>.json` (in release `map-data-uk-extra`, like limits, roadinfo, charge zones and search) | Lane counts and lane arrows, in half-degree squares, from OpenStreetMap only |
| `places-<row>_<col>.json.gz` | Businesses, places and streets, merged and de-duplicated, quarter-degree squares |
| `limits-<row>_<col>.json` | Speed limits on roads, in half-degree squares: `{"ways": [[kmh, [[lon, lat], ...]], ...], "src": ["o"\|"m", ...], "dir": [...], "source": "..."}`. `ways` is what the app reads; `src` says where each came from (`o` OpenStreetMap: the only source in phone files); `dir` holds limits known for one direction of a two-way road only (points in travel order; not used by the app yet). Roads with no known limit are left out |
| `limit-checks.md` (on the run's summary page) | Limits that look wrong, for the owner to check by driving |
| `roadinfo-<row>_<col>.json` | Road warnings and turn landmarks, in half-degree squares: speed bumps, hazard signs, speed cameras, level crossings, toll booths, schools, traffic lights, stop signs and well-known named places (fuel stations, pubs, places of worship, fast food, supermarkets). Same layout as an Overpass answer, so the app reads it with no signal |
| `search-offline-uk.tsv.gz` | Offline search (about 31 MB): every postcode in Great Britain (1.7 million), every named street, and towns, villages, stations, hospitals, schools, airports, ferry ports and motorway services, from OS Open Names. Format below |
| `cameras-uk.json` | Speed / red-light / average-speed cameras and level crossings |
| `charge-zones-uk.json` | Charge and emission zones with what each means for a car: London Congestion Charge and London-wide ULEZ (official TfL boundaries), Birmingham Clean Air Zone (inside the A4540 Middleway, traced from OpenStreetMap because the council's own file is not openly licensed), and the other Clean Air Zones / Scottish LEZs mapped in OpenStreetMap |

Also, in the separate `routing-uk` release: `routing-uk.json` and `valhalla-uk-<date>.tar.gz.partNN`, the
whole-UK road graph (Valhalla 3.6.3) for routes worked out on the phone with no signal. The phone
downloads it on Wi-Fi only when you switch it on in Settings; `routing-uk.json` switches to a new
build only after its pieces have uploaded and six test journeys across the UK have worked.

Sources and licences:
- © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, [ODbL](https://opendatacommons.org/licenses/odbl/)
- [Overture Maps](https://overturemaps.org) places (CDLA-Permissive-2.0 / Apache-2.0, including Foursquare data), merged with OpenStreetMap points (ODbL) and OS Open Names (OGL)
- Mapillary: not in any published file (owner, 18 Oct 2026). Street-photo detections may only be used inside DriveMate's own checks, to point out places for the owner to drive and confirm.
- Contains OS data © Crown copyright and database right (OS Open Names, Open Government Licence); Contains Royal Mail data © Royal Mail copyright and database right; Contains National Statistics data © Crown copyright and database right
- Places: Data from Foursquare. Copyright 2024 Foursquare Labs, Inc. All rights reserved. Available under Apache 2.0.
- Congestion Charge and ULEZ boundaries: Powered by TfL Open Data (London Datastore, Open Government Licence v2)

Full licence texts and exact credit lines: [licenses/NOTICE.md](licenses/NOTICE.md).

Source licence declarations are preliminary. `source-inventory.json` records observed
files and fingerprints, not legal clearance or complete feature provenance. Optional
upstream sources and Planetiler ancillary downloads still require review before new
live publication. Open access alone does not establish redistribution rights.

The app checks for a new generation daily while in use. Identical regional files stay
cached; changed files are downloaded when needed and checked before replacing the
previous file. This is a whole-file update, not a binary patch. The online PMTiles map
streams requested portions; an optional offline archive still needs a full replacement.

`scripts/publish_map_data.py` splits new generations across main and extra releases to
avoid GitHub's 1,000-asset limit. Published data assets are never overwritten. A failed
upload or checksum check leaves the previous pointer selected.

The bootstrap repair inventories the existing legacy assets, preserving their hashes.
It archives and verifies `build-info.txt` before removing that metadata from the full
legacy release to make room for `latest.json`. It does not regenerate road data or claim
that an earlier partial build covered every UK lane/place square. Missing regional data
remains unavailable, rather than being treated as an empty area.

### Offline search file format (`search-offline-uk.tsv.gz`)

Gzip-compressed UTF-8 text, one record per line, fields separated by TAB.

- Line 1 (header): `#drivemate-search-offline` TAB `1` (format version) TAB build date TAB credits (show these).
- Every other line: `key` TAB `type` TAB `name` TAB `area` TAB `lat` TAB `lon`
  - `key`: the name in lower case, accents removed, `&` → `and`, only a–z, 0–9 and single spaces
    kept. Postcodes have no space (`sk82ez`). **Lines are sorted by key** (byte order), so everything
    starting with what the driver typed is together: stream until past it, or binary-search an
    unpacked copy.
  - `type` (one letter): `P` postcode, `R` street, `C` city, `T` town, `V` village, `H` hamlet,
    `S` suburb/area, `O` other settlement, `N` railway station, `B` bus/coach station,
    `A` airport/airfield/heliport, `F` ferry terminal/harbour, `M` hospital/hospice/care home,
    `E` school/college/university, `U` motorway services.
  - `name`: as shown (`Menai Grove`). Welsh/Gaelic names are extra lines for the same place.
    **Empty for postcodes**: show the key in capitals with a space before the last three
    characters (`sk82ez` → `SK8 2EZ`).
  - `area`: `Town, Postcode district` (`Cheadle, SK8`), or the council/county when there is no
    town; for postcodes just the town.
  - `lat`, `lon`: WGS84, 4 decimal places (~10 m). Postcode = its centre; street = a point on it.
- Northern Ireland isn't in OS Open Names, so its postcodes and streets aren't in this file.

Example lines:
```
menai grove	R	Menai Grove	Cheadle, SK8	53.3956	-2.1942
sk82ez	P		Cheadle	53.3954	-2.1942
```

The map build uses no Mapillary data (no `MAPILLARY_TOKEN` needed).

National Highways transport snapshots are collected separately by
[road-intelligence.yml](.github/workflows/road-intelligence.yml). Powered by National
Highways’ Transport Data Feeds. See the [official API terms](https://developer.data.nationalhighways.co.uk/terms).
A daily snapshot is not live traffic and expires after ten minutes for live use.
No National Highways permanent speed-limit dataset is claimed.

## Daily publication and setup

This public repository contains data-building utilities, not private app source.
The new app reads the independent region catalog, selects available coverage by
location and verifies the region manifest and map identity. Catalog entries can
refer to older unchanged assets of the same region. A failed build or upload
retains the previously selected region generation.

Two UTC triggers plus a tested UK-time guard account for BST. Autumn uses the
first 01:00; the spring day without 01:00 uses 02:00. Initial expansion continues
automatically between daily checks, one region per job, until there is no more
work. No job constructs a whole-UK PMTiles file.

Required owner setup:

- App repository Actions **variable** `DRIVEMATE_MAP_RELEASES_BASE_URL`:
  `https://github.com/phuk-prog/drivemate-map/releases/download`. Rebuild the APK
  after changing it; installed APKs do not read build variables dynamically.
- Map repository Actions **secret** `NATIONAL_HIGHWAYS_API_KEY`, obtained through
  [National Highways subscriptions](https://developer.data.nationalhighways.co.uk/subscriptions).
  Missing setup is reported as `setup_required`, never as an empty traffic feed.
- DMN road packs need the private navigation-tools source, historically
  `phuk-prog/DriveMate-Navigation`, which was unavailable during recovery. Set Actions
  **variable** `DMN_SOURCE_REPOSITORY` to the restored private repository and a
  narrowly scoped read-only **secret** `MAP_BUILD_SOURCE_TOKEN` for checkout.
  Its code is used on the runner and is not published in this repository.
  The legacy automatic DMN builder remains disabled until a region-aware private source is configured; the regional Valhalla graph is published as a separate capability.

The regional workflow runs the full source regression suite and generator self-checks.
Before a region is published it verifies every generated map payload, checks detailed
roads at declared Stockport/Manchester samples, tests routes from the regional graph,
records source availability and binds validation to exact asset hashes. These checks
establish archive integrity and sampled regional behaviour, not full on-device or
complete real-world coverage certification.

## Additional information sources

| Information | Source and present integration | Access or remaining work |
|---|---|---|
| Roads, buildings, lanes, restrictions, limits, cameras, hazards, addresses | OSM via Geofabrik; generated by the map workflow | ODbL and attribution; source mapping may be incomplete |
| Regional maps and low-zoom national base | Generated from the same verified UK PMTiles | App regional-selection UI is separate |
| Places and businesses | OSM, Overture and OS Open Names | Sources and attribution recorded; upstream failures reported |
| Search and postcodes | OS Open Names; NI gazetteers where available | NI postcode reuse is not included |
| Congestion charges and emissions boundaries | TfL open boundaries; available mapped zones | Boundaries do not establish current tariffs or exemptions |
| Closures, roadworks and variable/temporary speeds | National Highways official DATEX II API collector | Key required; England strategic roads only; dated snapshots, not a live app overlay |
| Roadside signs | Existing OSM detail-layer builder | Runs after successful map build; app styling/integration needed |
| London disruptions | TfL Unified API, https://api.tfl.gov.uk/ | Candidate for subsequent geocoded overlay integration; not yet collected here |
| Utility streetworks | UK Power Networks open-data portal | Candidate with partial coverage; not all national roadworks |
| Scotland, Wales and Northern Ireland traffic | Respective official traffic authorities | Confirm feed access, licence and geocoding before adding; no coverage claimed |
| Live traffic speeds and travel times | TomTom traffic APIs | Requires a separate key and redistribution terms; not mirrored into public GitHub |
| Current toll prices, parking and fuel prices | Relevant official/operator feeds | No comprehensive free licensed source verified; do not invent values |

Daily downloads do not turn into live traffic merely by being included in a map.
The app must obtain current observations, honour expiry, and distinguish advisory
speeds, mandatory limits, planned works, suspended events and completed closures.

## Licences

Licence texts (ODbL 1.0, CDLA-Permissive-2.0, Apache-2.0, the Foursquare notice) and every required
credit line are in [licenses/NOTICE.md](licenses/NOTICE.md). Each map-data release carries them as
assets (`LICENSE-*.txt`, `NOTICE-Foursquare.txt`, `NOTICE.md`).
