# TF-06 routing publisher recovery — 28 October 2026

Status: source correction prepared; real graph build/publication and TF-06 acceptance OPEN.

## Observed cause

At map main `64f1bdba48bd61864821acd4ec4657a6beb1a453`, GitHub reports `map-data.yml` as `disabled_manually`. Its only run `39928542974` at `3059a1a4832a1f8a77f296b174b9699b39a05bc3` was cancelled in Planetiler; its dependent routing job never started. Successful `stop-legacy-uk.yml` run `39930888472` at `601acd54343f4801429de1e1da2d3f9757c25bd7` explicitly cancelled that run and disabled the entire legacy workflow on 23 October. The regional display-map replacement does not produce the Valhalla graph. The only national Valhalla publisher therefore became unreachable along with the superseded display-map builder.

Authenticated release-by-tag inspection still returned HTTP 404 for map `routing-uk` on 28 October. Existing register preserves unauthenticated 404 checks for both map/data repositories. No graph byte integrity or national routing coverage accepted. Do not change Android `RoadPack.BASE` until the intended public release exists and its manifest and chunk bytes are verified.

## Prepared correction and bounded verification

`routing-uk.yml` is a separate manual entrypoint retaining the existing routing job's steps, graph engine, restriction rewrite, six journey smoke checks, connectivity diagnostic and immutable verified publisher. It defaults to `validate_only=true`. The disabled legacy workflow remains disabled. Both use concurrency group `map-data`, preventing overlapping pointer publication if the legacy workflow is ever deliberately enabled. Publication still requires main plus explicit `validate_only=false`.

Frozen entrypoint regressions: RED 0/2 (no independent workflow), GREEN 2/2. Existing routing publication integrity regressions: 7/7. Canonical map preflight with the exact CI dependency versions: 451/451 tests, zero failures/errors/skips and five generator selftests passed. Initial local environment lacked optional test dependencies; isolated venv installation resolved those environment errors without production changes. These are source/contract checks, not an actual graph build or Android compatibility acceptance.

## Next concrete execution

1. Complete canonical map preflight and exact-head public source CI, then merge according to repository gates.
2. Run the dedicated workflow with `validate_only=true` on the reviewed main commit. Existing timeout is 300 runner minutes. It downloads the whole UK OSM PBF once, rewrites restrictions, builds Valhalla 3.6.3 graph, and executes six real journey smoke checks plus connectivity diagnostics. No release pointer changes in validation mode. This is a substantial build; actual RAM/disk sufficiency and duration remain unmeasured for this standalone run.
3. Retain source fingerprint, retrieval date, graph hash/size, engine/toolchain versions, journey/connectivity results and validation receipts. Preserve OSM attribution and ODbL source-distribution obligations; exact release legal clearance remains separate. Six journeys do not establish every road or legal restriction.
4. Only after successful validation and required publication authorization, publish an immutable graph generation with the existing verified publisher, confirm public `routing-uk` pointer and stream/hash every real chunk plus decoded graph. Then correct Android endpoint in a separately tested change.

Android currently uses `valhalla-mobile:0.6.3` and `valhalla-models-config:0.5.2`, ABIs arm64-v8a/x86_64. No native dependency changed; actual newly built graph engine/ABI/device compatibility remains to be observed on production loader and emulator, then owner device. No signed build is authorized by this patch.

## TF-07–10 readiness

- TF-07 remains blocked on public graph bytes, production endpoint correction and install. Existing `ci/real_offline_acceptance.py` has distinct required stages for public graph, production-store install and actual guidance screen; preserve this separation. Run one future session bound to the same source/release/app/test hashes, network isolation and screen/voice receipts. PMTiles install plus bundled Manchester routing cannot be combined as national acceptance.
- TF-08 can proceed independently for companion feature/road-detail/restriction/lane/place pack source-to-consumer coverage and corrupt/interrupted download recovery. Reuse prior 52-region actual-byte proof; no ~1.6 GB re-download without changed releases. UK graph consumer install/recovery still depends on TF-06.
- TF-09 can prepare independent nation/address/road cohorts and NI border feature checks while graph build is pending. File counts and six journeys are insufficient denominators; rights and legal truth remain explicit unknowns.
- TF-10 cannot claim online/offline parity until TF-07 supplies the same installed national graph and frozen authentic UK journey cohort. Prepare case IDs and denominator now, but report unavailable offline cases as blocked, never passed.

Category 1 and J27-03 remain OPEN; no release, device or whole-UK acceptance claimed.
