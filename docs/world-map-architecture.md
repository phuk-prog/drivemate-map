# DriveMate — worldwide seamless map and continuous map-data operations

Status: ARCHITECTURE AND SAFE WORKFLOW IMPROVEMENTS; not yet a worldwide production map.
Canonical task history and sign-off remain in phuk-prog/drivemate-app/REGISTER.md.

## One map on screen, modular map data underneath

Show ONE continuous worldwide map at every zoom. A seam between countries/regions
must never create a blank rectangle or an unverified route. Do NOT require a
literal single maximum-zoom planet archive. A world-scale detailed vector map
may exceed 100 GB, and GitHub Releases requires each asset to stay under 2 GiB.
Keep a global low-zoom overview and independently versioned, small geographic
high-zoom shards. A verified index chooses the right layer by longitude,
latitude and zoom, including the anti-meridian. The app can cache visible
tiles online and install user-selected regions offline. It must never silently
download the entire planet onto a phone or add a paid hosted dependency.

Keep the current tested UK map, current national release pointer, UK offline
support, navigation and original-key signed APK unchanged until the new global
path is proven at equivalent or better quality. This request supersedes
UK-only DISPLAY coverage as a product goal; it does not make worldwide legal
road guidance, full offline navigation or global POI search already available.

## Information and safety

Ingest all relevant fields that are ACTUALLY available, current, safe and
legally redistributable for each source and country: road geometry, legal
access/turn rules, verified lane connections and limits, pedestrian roads,
water/terrain, buildings and heights, names, addresses, place search,
junctions, road surfaces and sourced warnings. Separate map appearance,
routing, destination search and alert datasets by contract and freshness.
Provenance contains upstream source, acquisition time, version/replication
clock, permissions, data type, coverage polygon, schema version and identity.
Different countries have different data quality and legal restrictions.
UNKNOWN is not a failed road, but unsupported manoeuvres/lanes must not
be recommended. No assertion of literally every possible data source.

## Verified publication contract

One logical index, multiple immutable checked pieces. For each region:
input checksum + real applied OSM delta + supplemental source version +
pipeline fingerprint -> canonical content digest. When that digest and
relevant pipeline/supplement versions match the last published generation,
REUSE the existing immutable map asset; only an observation/checkpoint may
advance when needed to stop duplicate retries. A changed timestamp or a
successful workflow run alone is NOT new geographic data.

For a changed region: build one region, verify input identity before/after
download, validate map schema, source rights, seam samples, tile coverage and
SHA-256, upload immutable assets, confirm read-back metadata, THEN atomically
promote one small manifest/index. All readers use a pinned generation during
a trip. Failed or partial uploads never replace the last working generation.
Keep at least two verified map generations for safe rollback, not endless
duplicates. Do not delete legacy assets still referenced by published apps.

## Scheduled GitHub operations and recovery

- Exactly two NORMAL update checks at 01:00 and 14:00 UK local time using
  summer/winter schedule guards (two pairs of UTC crons, one selected per
  pair per date).
- Hourly watchdog is a cheap CHECK, not a third regular map publication.
  When a scheduled job was missed or an isolated failed region remains,
  it may safely enqueue only missing work; active/queued jobs are not
  duplicated. All running jobs retain source and release receipts.
- Avoid automatic full-map rebuilds on every source/script push. PR and
  main source preflight remain active. Real data changes are processed by
  the two daily schedules or a manual urgent dispatch. Serialize release
  promotion; bound region parallelism and use per-region retries/backoff.
- If the live catalogue cannot be read or fails schema validation, alert
  via failed GitHub Actions and KEEP the last verified publication; do
  not pretend an empty catalogue means all updates are done. 403/429
  exhausted quota, upstream source change during download, checksum,
  shape, licence and render errors all fail closed. Retry after quota
  reset, not with infinite immediate API calls.
- Every scheduled run emits changed/unchanged/unknown, reasons, source
  fingerprint, exact map SHA, count of changed regions, failed regions,
  reused releases and whether a pointer was promoted. The Github
  Actions workflow history is the primary automatic monitoring record.

## Removing redundancy without data loss

The source audit found 22 workflow YAML files. The old map-data.yml is a
historical builder and should not start scheduled or push-triggered duplicate
builds. The current regional-maps.yml and uk-overview.yml should be scheduled
twice daily and manually dispatchable, not triggered again by unrelated
script pushes. source-checks.yml remains active for PR/main static tests;
map-watchdog.yml remains hourly for missed schedules. Keep uk-map.yml,
uk-join.yml and dmn-pack.yml as independent outputs only when their input
identities changed, and do not mix display maps with routing packs.

Already in the producer: canonical regional-content comparison avoids binary
uploads for identical regions, and one UK-map pointer compares input region
manifest digests. This change also prevents overwriting an unchanged mutable
regional catalogue when a redundant promotion would produce identical bytes.
Do NOT remove old UK releases, all old code/tests, licence notices or proof
files without verified reference scanning and a rollback plan. Remove only
confirmed duplicated automation triggers in this first safe cleanup batch.

## Staged worldwide delivery (do not skip gates)

W01: record accepted worldwide open data sources and licences; global zoom
overview source and compatible layer schema; anti-meridian/world bounds;
published data size budget and fallback. Independently validate the exact
source and geometry against real locations across continents.

W02: produce global low-zoom immutable map shards with a small validated
world pointer; test schema, road visibility, labels, oceans and seams.
No production app switch until rendered results are verified.

W03: add global tile-source selection/mosaicking into the app behind an
explicit flag; preserve UK path and offline map. Test zoom transitions,
online loss, disabled network, partial-download recovery and no false roads.

W04: detailed high-zoom source-backed regions, on-demand online range reads,
area-specific offline installs, search/guidance overlays with explicit
coverage and legal confidence; never invent data where unknown.

W05: real-device acceptance, full navigation regression, exact source SHA
test receipt, compatibility, app performance and signing. Only then claim
global availability for the actually published regions.

Current truth: **Category 1 — World map and downloads: Part done.** The UK map subcategories are complete; worldwide subcategories are not. The proven
automated map/testing work so far applies to the UK; worldwide sources,
global basemap, globe rendering and worldwide offline coverage remain PENDING.
