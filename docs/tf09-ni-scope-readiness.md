# TF-09 NI scope and rights — bounded update, 28 October 2026

Status: evidence preparation; geographic acceptance OPEN/UNKNOWN.

At map commit `64f1bdba48bd61864821acd4ec4657a6beb1a453`,  progressive_regions.py defines NI-area rectangle [-8.2,54,-5.3,55.5] from Ireland+NI PBF. Existing category evidence already establishes rectangular complete-ways extraction, not authoritative border clipping. No new feature failure count claimed.

Live official catalogue retrieval: https://www.data.gov.uk/dataset/b2ea6283-a3de-47b7-a7e2-930d7b5d6121/osni-open-data-50k-boundaries-ni-outline3 returned 200. Catalogue updated 8 July 2026, identifies mid-scale vector NI outline, links UK OGL v3 but explicitly requires LPS Open Government Data Licence. Actual listed Esri REST resource is https://services.spatialni.gov.uk/opendata/rest/services/OSNIVector/OSNIOpenData_Boundaries_50K/MapServer/0 (resource date 13 May 2021). Direct metadata request ?f=pjson returned 503 Service Unavailable; no geometry bytes/digest verified. Catalogue date does not establish geometry revision.

Next bounded work: obtain real metadata/geometry under restored service or independently corroborated licensed alternative; retain raw retrieval/hash/license/attribution/version before public frozen fixture. Then audit actual published NI feature coordinates and cross-border way/relation handling. A 1:50k outline cannot alone establish road-level border truth. Never discard legitimate cross-border ways or infer Republic features from bounding box alone. Build a denominator from independently selected real border approach/crossing cases and report within/outside/border-intersecting/unknown separately. No national completeness percentage from 52 assets or six routing smoke journeys.

Reuse existing 52/52 PMTiles + 52/52 manifest byte integrity; no 1.6 GB download repeated. Exact release rights, NI feature inclusion, road/address coverage and legal truth remain UNKNOWN. No source or clipping change is justified while polygon bytes/rights and actual asset findings remain unavailable.
