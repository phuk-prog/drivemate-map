# TF-10 online/offline parity cohort readiness — 28 October 2026

Status: cohort candidates prepared; all parity cases NOT RUN. TF-07 national production graph installation and guidance acceptance remain prerequisites.

Reuse the existing six real endpoint pairs from `.github/workflows/routing-uk.yml`; preserve their coordinates and smoke-test history. They are real UK locations, but endpoint snapping, specific road choices and legal manoeuvres have not been independently established. Each currently has online result UNKNOWN, downloaded-national-offline result BLOCKED, and legal truth UNKNOWN. Do not count existing smoke checks as parity results.

| Candidate ID | Existing real route | Start latitude, longitude | End latitude, longitude |
|---|---|---|---|
| TF10-01 | Stockport–Liverpool | 53.4052, -2.1575 | 53.4075, -2.9919 |
| TF10-02 | Stockport–Sheffield | 53.4052, -2.1575 | 53.3781, -1.4697 |
| TF10-03 | Manchester–Wrexham | 53.4774, -2.2309 | 53.0466, -2.9925 |
| TF10-04 | Carlisle–Gretna | 54.8925, -2.9329 | 54.9947, -3.0646 |
| TF10-05 | London–Cardiff | 51.5074, -0.1278 | 51.4816, -3.1791 |
| TF10-06 | Belfast–Lisburn | 54.5973, -5.9301 | 54.5162, -6.0580 |

These six are a bounded first cohort, not full UK coverage. Existing `scripts/routing_connectivity.py` has eleven additional real diagnostic corridors that can be considered after the initial cohort; do not duplicate all probes or treat route-found as legal acceptance.

## Freeze before execution

Record exact app, public-suite, map publisher and graph build commits; graph source hash/date, immutable release generation, manifest/chunk/decoded graph hashes, production-store selected hash, map/feature versions, routing engine/version, request profile/options and timestamp. The same production app and same installed offline graph must underpin both runs. The online provider's graph/version must be recorded or explicitly UNKNOWN; when the provider cannot expose/freeze it, report behavioural comparison with an uncontrolled provider version, not equivalent-engine parity.

Use identical start/end and frozen authentic replay samples for each pair. Existing endpoint pairs alone are not GPS trajectories; do not interpolate invented legal junction movement or substitute the controlled route geometry in `LaneJourneySessionScreenTest.kt` as independent road truth. The J27 M60 incident checklist requires actual verified approach/exit geometry; screenshot labels do not identify the driver's exact route.

## Required separate observations per case

1. Observe online request/response and actual production navigation session, route geometry, instructions, lane availability/decisions, reroute and arrival. Pin source IDs and available provider version; record errors/retries separately.
2. Select the verified downloaded national graph in production store. Observe device network reachable → blocked before/after replay → recovered, then the actual offline route/session, visible guidance and applicable speech using that graph.
3. Compare legality only where independently dated evidence exists. Compare instruction timing, lane availability, route/reroute/arrival behaviour and explicit source/unavailability/fallback reasons separately; different lawful routes need not be identical geometry. Missing legal truth is UNKNOWN, not software failure.
4. Bind both outputs, screenshots/voice observations and network/store receipts to the same case ID and frozen inputs. Never combine Stockport display-PMTiles installation with bundled Manchester routing as downloaded-national proof.

Report per subsystem correct/eligible-total, failures, unknown, provisional, skipped and blocked. Initial candidate denominator = 6; executed = 0; no parity score or percentage is available. National graph release, production endpoint correction/install and TF-07 source→guidance acceptance remain blocked. Owner-phone/car acceptance is TF-19 and cannot be inferred from emulator proof. Category 1 and J27-03 remain OPEN.
