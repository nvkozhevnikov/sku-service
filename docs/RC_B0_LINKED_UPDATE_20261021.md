# B0 — scoped supplier fact updates

DONE/APPLIED: the nine wrapper SourceBackend calls now supply their running
source_run_id. The passive repository validates its owning running queue job
inside the same transaction as source/offer updates, captures and observations.
Legacy passive callers still reject fresh catalog-linked writes.

Only supplier facts are writable. Existing source/offer PKs, supplier namespace,
catalog_product_id and Sterbrust Product ID are preserved. A known URL with a
different external ID, missing existing offer, target payload, failed target
readback or semantic conflict raises and rolls back. There is no rebind fallback.

Existing targets are checked directly by the unchanged F01/F03 plan_card.
An unchanged previously linked ambiguous identity may refresh its commercial
facts without acquiring a new AUTO acceptance. New semantic facts require AUTO
against that same target; hard CONFLICT always stops. Unknown price/stock does
not erase proven values. Existing offer activation is preserved; new passive
offers remain inactive. No catalogue, match or supplier activation writes occur.

Actual stored details and commercial columns drive material history. An exact
capture is a no-op; a fresh unchanged capture adds observation evidence, with no
business event. Changed facts use existing decimal-safe history helpers and
detail writers. Native Partner-ST/Optimum ingest implementations are unchanged.

VERIFIED means offline cursor-contract and fake-transport integration only.
Final focused result: 475 PASS / 0 FAIL / 0 SKIP (2026-10-21), including
all nine linked/unlinked fixture paths, native regressions and writer-to-resume.
Run `scripts/qa_source_runtime_offline.py --b0-only` in the project environment.
The canonical B0 report and separately gated SQL QA plan live in workspace
`reports/RC_B0_LINKED_UPDATE_FIX_20261021/`. No SQL/LIVE approval is implied.
