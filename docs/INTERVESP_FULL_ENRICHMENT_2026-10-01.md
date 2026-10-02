# Full Intervesp integration: checkpoint and output contract

## Evidence labels and boundaries

- `LIVE-VERIFIED`: portable RC PostgreSQL 17.11, `127.0.0.1:55449`, `universal_supplier_server`, system identifier `7691270601420084116`; fresh exact-path read-only preflight precedes source access.
- `APPLIED` / `LIVE-VERIFIED`: 1642 pinned archived listings ingested into RC, all 80 previous source and offer IDs preserved; zero duplicate identities, disabled commercial suppliers, inactive commercial offers, no commercial canonical links.
- `PACKAGED-EVIDENCE`: Grok HEAD `613000e0f2962c6ba108bbe6903e415fb716d635`, 295 sanitized listings cover all 1642 URLs. Never repeat discovery/listing crawl.
- `REPOSITORY-VERIFIED`: final focused offline regression report `reports/RC_LOCAL/INTERVESP_FULL_2026-10-01/FINAL_REGRESSION.xml` (317 tests and 27 subtests PASS; upstream TestClient deprecation warning).
- `NOT RUN`: real browser QA in this continuation; computer-use security policy rejected the local tab and was not bypassed.

The running checkpoint, not this document, is authoritative for progress:
`reports/RC_LOCAL/INTERVESP_FULL_2026-10-01/DETAIL_ENRICHMENT.json`.
Do not rerun a collector that is still running. No PostgreSQL start/stop from the restricted agent context.

## Identity, matching and review

Intervesp canonical product URL is a supplier-scoped deterministic identity. Legacy internal IDs remain linked to the same existing PKs; listing evidence does not fabricate detail-page internal IDs. URL/title/full-execution contradictions quarantine detail evidence rather than silently changing identity. One isolated timeout remains REVIEW with its immutable checkpoint; no response is not MISSING. A second transport error, access error or DB failure stops the resumed job.

Existing requires the current conservative matcher and a real saved-registry Product ID. Explicit axis count/interpolation contradictions or incomplete counterparty execution evidence block automatic confirmation. Known suffix/accessory regressions remain non-auto. Current confirmed/conflict decisions are not overwritten by advisory characteristics or section learning.

NEW keeps `NEW_CANDIDATE_ID` per source and an independent deterministic `NEW_GROUP_ID`. Strict full execution, compatible brand/context and independent identity anchors are necessary; fuzzy similarity alone is not evidence. Ambiguous equivalent-family cohorts are REVIEW, not partially auto-grouped. One source can belong to only one group. A strictly validated singleton is a technical group, not proof of cross-source equivalence. These identifiers never become Sterbrust Product ID or ESOL update keys.

Readiness gates:

1. Strict group/singleton identity and dedup evidence.
2. Exhaustive pinned active/inactive canonical absence audit (59500 snapshot products, not a fresh live absence guarantee).
3. Category-scoped SAFE SECTION evidence learned only from current confirmed Existing pairs.
4. Typed characteristic identity evidence without semantic contradiction.
5. `READY_TO_CREATE_IDENTITY`.
6. Checked description, characteristics, images and document links, nonempty required content, observed price/availability states and detail provenance.
7. `READY_TO_CREATE_FULL`.

Document/image evidence means observed source URLs/metadata; it does not claim binary assets were downloaded or independently availability-checked. Empty documents are valid only when the source document area was actually checked. Missing/request prices remain NULL, not zero. Unknown availability is an observed uncertainty, not inferred stock.

## Identity-first completion pipeline (operator update)

The original 454-detail plan is not a mandatory crawl quota. Operator requested a safe pause at 284: 125 identity-critical / 28 price-only / 17 characteristics-only / zero price+characteristics remain. Original checkpoint/SHA and fresh SQL pause proof are preserved in `IDENTITY_FIRST_REPLAN.json` and `DETAIL_PAUSED_284.json`.

Resume only `--identity-critical-only --resume-paused`. Minimum 125 GET / 2500 seconds of pauses; responses add time. A cooperative `PAUSE_REQUESTED` file is checked only between cards, never during GET/COMMIT. Final matching is permitted after every pinned identity-critical URL is assessed (successful detail or quarantined REVIEW); price/content incompleteness stays explicit. No repeated saved detail pages.

After identity-critical coverage closes:

1. `verify_intervesp_saved_replay.py --apply-replay`: all 1642 saved listings plus actually fetched successful details, backup first, fresh identity guards, advisory lock, exact no-op database replay; counts and current commercial projection must remain identical. Deferred buckets are recorded. Zero public GET. This is explicitly not full live RUN2.
2. `publish_full_intervesp.py --output <new versioned final directory>`: read-only snapshot, full matching, strict NEW/characteristic/absence gates, no matching/selection SQL mutation; refuses partial coverage and existing output paths.
3. `build_proposed_workbooks.mjs`: four diagnostic XLSX views from that final JSON, followed by render/visual inspection.
4. `verify_full_intervesp_outputs.py --final ... --replay ... --publish-panel`: validate all actual files, hashes, XML UTF-8, rows, identities, NEW groups, NULL prices and exact saved replay; preserve old panel index and files, atomically expose a versioned local result.

Selective reassessment is complete: after closing the 125 identity URLs, one Existing price gap was fetched. Fresh used-condition evidence changed that proposal to REVIEW against a condition-unspecified canonical card. Final matching is 136 Existing / 259 Conflict / 1247 Review; no create group passes all identity/absence/section/characteristic gates. The remaining plan is zero useful GETs, with 27 price-only and 17 characteristics-only URLs deferred. Never fetch those 44 simply to make the original plan complete. `READY_TO_CREATE_IDENTITY` does not pretend complete content, and matching all 1642 does not pretend all details were fetched.

The previous 255-URL RUN1/RUN2 evidence remains preserved. New full live RUN2 is deferred by the approved fast-path strategy; two bounded live rate-test cards plus exhaustive saved replay are reported separately, never relabelled as a full live rerun. Robots requires 20 seconds; faster test was clamped, no parallel workaround.

## Files

- `UNIVERSAL_SUPPLIER_NEUTRAL.xml`: all five available supplier namespaces, independent observations and source content/provenance, review retained as uncertain registry records. Historical Partner-ST/Optimum prices are separately labelled restored snapshot values, not current public prices.
- `STERBRUST_EXISTING_PRICE_AVAILABILITY.xlsx`: all commercial confirmed Existing records, including NULL/request prices, real Sterbrust ID/name and evidence.
- `STERBRUST_UPDATE_EXISTING_PROPOSED.xml` / `STERBRUST_UPDATE_EXISTING.xlsx`: confirmed identities; workbook deterministic price proposal is diagnostic, not applied offer selection.
- `STERBRUST_CREATE_NEW_CANDIDATES.xml` / `.xlsx`: one create proposal per strict ready group, actual proposed SECTION_ID, independent supplier sources. No fabricated Product ID.
- `STERBRUST_REVIEW.xlsx`: unresolved matching/grouping/readiness/HTTP reviews and conflicts, with reasons/evidence.
- `MATCHING_REPORT.csv`: full commercial source-row decisions, exclusive Intervesp terminal counts and supplemental gate reasons.
- `MATCHING_FINAL.json`, typed/absence/group evidence JSON and `FINAL_FILES_VERIFIED.json`: machine-readable audit; no synthetic runtime history.

## Operator panel

After verified versioned publication, restart only the panel in a separate ordinary PowerShell (do not close the PostgreSQL window):
`& 'D:\documents\personal\ChatGPT\Стербруст\work\sku-service\scripts\start_local_rc_ui.ps1'`.
Secure password entry is local; never put password/session secrets into reports or Git. Existing listening-panel instance must be stopped normally first. Do not start PostgreSQL or enable write flags through this instruction.

Open `http://127.0.0.1:58097/`, then “Сопоставление и файлы”. Updated code shows counts, NEW group/source membership and identity/full readiness separately, plus full neutral XML and price/availability downloads. A human browser check remains required where computer-use is unavailable. Production write, ESOL import, scheduler, Docker, deployment and GitHub push remain outside this continuation.

## DONE / APPLIED / VERIFIED

DONE: identity-first ingestion/matching, selective enrichment reassessment, four workbooks and supplier-neutral/proposed XML/CSV.
APPLIED: isolated RC source ingestion (4362 products/offers, 2486 captures/observations), local final files and versioned panel index only. No live matching/selection mutation or production action.
VERIFIED: exact RC identity/safety, 1902 saved SQL replay no-ops, 317 tests + 27 subtests, actual file hashes/rows/UTF-8/NULL-price/identity checks and four rendered workbook previews. Browser verification remains NOT RUN; full live RUN2 and complete content enrichment are not claimed. See `reports/RC_LOCAL/INTERVESP_FULL_2026-10-01/REPORT.md` and `FINAL_PROPOSALS/FINAL_FILES_VERIFIED.json`.
