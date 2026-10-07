# KAMI OFFLINE RUN2 contract — 2026-10-06

## Latest verified execution (supersedes preparation-only state below)

OPERATOR-CONFIRMED attachment74f6aee9: two narrow P2 fixes, then actual offline
execution if regression/fresh gates PASS. Migration DB SHA must equal exact
Git HEAD blob; working bytes may differ only by CRLF/LF representation. No
trimming/BOM removal/comments/whitespace/encoding/SQL normalization.
Legacy absent commercial hash accepted only for site9270806/PK7187,
site9807314/PK7186,variant9833201/PK7188,variant9833203/PK7189 after exact prior
RUN1 snapshot/PK/capture/content and independent source/offer/observation proof.
Proof covers price/old-price decimal semantics, currency, raw/normalized
availability, quantity/state/raw/role, price-state/basis/raw/source/group,
provenance, immutable observation fingerprint/time/reasons/region. No hash
backfill. Unknown missing hash or any non-null hash mismatch remains FAIL.

REPOSITORY-VERIFIED120PASS+43subtests; LIVE-VERIFIED fresh full preflight PASS.
Actual5264-row OFFLINE RUN2 completed, exact no-op across all public tables and
sequences; inserts/deletes/fact/audit changes0, HTTP0. Independent postcheck
confirmed original5264 PKs and RUN1 hashes; canary missing hashes still absent.
Evidence: OFFLINE_RUN2/CHECKPOINT.json and P2_GUARDS_FIXED_2026-10-06/
POST_RUN2_VERIFIED.json. This is not live supplier freshness proof.
KAMI-only matching input gate now accepts this real offline proof, never prep
or forged live completion. Frozen predecessor SHA20c3aefd... preserved.
Matching then stopped on a new P2 advisory caller generating NEW ID for a
model-less REVIEW. No final matching/export/selection/dump/restore or release
verdict. Resume latest P2_GUARDS_FIXED_2026-10-06/CHECKPOINT.json.

OPERATOR-CONFIRMED: preparation only, frozen evidence, supplier HTTP=0.
No actual RUN2 persistence until original isolated DB returns and fresh preflight
confirms the previous RUN1. No initdb/restore/substitute DB. No develop/production
change, matching, exports, scheduler, deployment, Sterbrust/ESOL writes.

## Two distinct runners

* `scripts/run2_kami_products.py`: original LIVE runner, unchanged; requests
  robots and product pages. NOT authorized under the current offline contract.
* `scripts/run2_kami_offline.py`: frozen-only runner; no HTTP fallback. Default
  invocation validates filesystem evidence only, with no database connections.
  Future `--execute` is a distinct explicitly authorized operation, NOT part of
  preparation and NOT executed merely because a DB becomes reachable.

From the KAMI feature worktree with Python3.12 and repository PYTHONPATH:

```powershell
$env:PYTHONPATH=(Get-Location).Path
& '..\sku-service\.venv-audit\Scripts\python.exe' -X utf8 scripts/run2_kami_offline.py
```

## Frozen pinning

Contract version: `kami-frozen-offline-run2-v1`. Expected identities:5264.
Explicit source-continuation/checkpoint, reconciled CARDS/manifest, original
collector checkpoint, verified RUN1 ledger/snapshot, approved target marker,
old-namespace baseline and persistence dependency SHAs are pinned in the runner.
Live runner SHA remains pinned separately. Runtime branch must be KAMI feature;
protected refs must match their original baselines.

Every selected capture is checked against its original SHA/URL/time/card identity.
Exactly5264 unique source identities, capture assignments and original source PKs
are required; no new PK is generated from historical records. No reparse/crawl.
The exact filename inventories of the three existing capture directories are
pinned:5518 historical files including5264 selected product captures. Existing
unselected historical captures remain non-actionable; new/missing inventory
entries fail closed. The check does not declare arbitrary unrelated workspace
directories to be part of this frozen source bundle.

## Network and DB guards

HTTP requests are hard-disabled at requests/urllib/http.client boundaries.
Python socket connect/connect_ex are denied except the explicit local DB socket
during future execution. Validation denies even that socket. libpq is C-backed;
therefore exact DB config and fresh actual server identity/role/path checks are
also mandatory. This is application-level defense, not an OS firewall claim.

Future execution first proves127.0.0.1:55451/universal_supplier_kami_rc/systemID
7693098812512405428/PG17.11/approved data path and all15 migration SHAs. It checks
the previous RUN1 recorded table/sequence snapshot, old five namespace baseline,
5264 source products/offers, disabled supplier/inactive offers/no canonical links,
the exact original source/capture PKs and stored content/commercial fact hashes.
Missing capture in DB is a STOP, not permission to insert it.

The actual replay calls KAMI-only persistence inside one outer transaction.
Each item must return exact_noop with its original PKs. All public table rows and
sequence fingerprints before/after must match; any drift rejects the transaction.
Expected semantic changes0, inserts0, deletes0, audit/timestamp changes0. Audit-only
changes are NOT silently tolerated or called semantic no-op by this version.
PostgreSQL sequence allocations are not rollback-safe; an unexpected insertion
bug must be reported, never repaired by resetting a sequence.

## Evidence levels / STOP

Fixture/mock/in-memory tests and full filesystem validation prove preparation
only: REPOSITORY-VERIFIED, not LIVE-VERIFIED RUN2 or current DB state. Actual
execution has not occurred. No downstream matching gate is changed to accept
preparation evidence as a completed RUN2. The existing live matching gate is
left untouched; integration of a real offline-run2 proof is a later scope.

Resume from `reports/KAMI_INTEGRATION_2026-10-05/OFFLINE_RUN2_PREP_2026-10-06/CHECKPOINT.json`.
Next: operator restores availability of the same existing cluster; fresh preflight
and previous RUN1 confirmation; only then separately authorized offline execution.
