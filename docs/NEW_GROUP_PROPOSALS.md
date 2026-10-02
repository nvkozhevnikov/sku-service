# Cross-source NEW deduplication proposal — 2026-10-01

`DONE`: isolated component `universal_supplier/new_group_proposals.py`, entry point `propose_new_groups(rows)`.
`APPLIED`: source code, offline tests and this report on the current integration branch only.
`VERIFIED` / `REPOSITORY-VERIFIED`: 41 new regression cases; combined matching/proposal regression **129 passed, 4 subtests passed**. Real-source final matching has not been rerun.

## Rules

- Scope: `intervesp`, `beka_mak`, `beka_mak_tr`; only upstream `NEW_CANDIDATE` records without an existing/proposed canonical ID can enter a group.
- Required input: `source`, `external_id`, original `new_candidate_id`, explicit `brand`, full `model`, `name`, known `product_kind`, evidence-backed `model_role=OWN_MODEL`, `source_url`, sanitized `evidence_ref`; optional full `execution`, typed `properties`, manufacturer article with its own `manufacturer_article_evidence_ref`. Brand must come from the source evidence, never a supplier-name default.
- Compatible explicit brand aliases (including BEKAMAK/Beka-Mak), exact normalized full model/execution and equal known product context are mandatory. Significant suffixes remain part of identity. No fuzzy similarity is used.
- Each pair needs an independently evidenced equal manufacturer article, or at least one shared, parseable identity-critical characteristic beyond generic electrical ratings. Model text repeated as an article and vendor internal IDs do not supply that proof. Unknown/malformed property evidence stays REVIEW.
- Existing `classify_identity_quality` semantic guards and category-specific typed property policies are reused. Contradictions in names, model ownership, execution, product type, properties or upstream semantic evidence block grouping.
- All pairs must pass; equivalence cannot be inferred transitively through a third card. Ambiguous related variants, duplicate/conflicting source identities or missing evidence quarantine the related input cohort. Base-model retrieval is used only to find potential contradictions, never to confirm equivalence.
- An identical source-record replay is a no-op. A supplier product can appear in at most one returned group; conflicting copies and repeated candidate IDs are REVIEW. Multiple different products of one supplier are not silently collapsed.
- Logical `NEW_GROUP_ID` is serialized as `new_group_id=NEWG-<SHA256 prefix>`, computed from rule version, normalized brand, full model, product kind and own-model role. It is independent of input order, price and supplier membership, so adding a proven third source preserves the ID. Every source record retains its own `NEW_CANDIDATE_ID`/`new_candidate_id`.
- A strongly evidenced single-source candidate is returned in `ungrouped` with no group ID. Insufficient single-source identity is REVIEW. Grouping never creates a Sterbrust Product ID.

## Offline QA

Command from the repository root:

```powershell
python -m pytest -q -p no:cacheprovider tests/test_new_group_proposals.py tests/test_proposal_export.py tests/test_proposed_matching.py tests/test_matching.py tests/test_stage6b.py
```

Final result: **129 passed, 4 subtests passed**. Includes the three-source positive group, TU2304V/base, LX20 Pro/NEW, HCV125 jaws/vise, WP1/WP2/base/ECO, missing brand/model, order and membership stability, typed units, conflicts, replay, candidate-ID collisions, transitive bridges and preservation of existing REVIEW/CONFLICT. Two existing report-fixture tests initially encountered sandbox `PermissionError`; the identical offline suite passed in the permitted normal execution context without altering those tests or their baselines.

## Connecting after Grok evidence

1. Run final canonical matching with existing quarantine/semantic guards. Enrich the resulting NEW rows with real brand, sold-item model ownership, product context, properties and sanitized evidence references. Current proposal rows do not contain all those fields; do not manufacture defaults to make them pass.
2. Call `propose_new_groups(enriched_new_rows)` once for the complete cohort. Keep `review` rows out of confirmed create output. Keep strong single-source `ungrouped` candidates under their existing candidate IDs.
3. Serialize **one** proposed NEW record per returned group, retaining its `new_group_id`, all individual `new_candidate_id` values, independent supplier identities and pairwise equivalence evidence in `source_records`.
4. Independently revalidate canonical absence and one unambiguous real Sterbrust `SECTION_ID` for the entire group. Group equivalence alone does not prove NEW or authorize creation. Section disagreement or a plausible canonical match remains REVIEW.

The component is deliberately not wired into existing final export generation yet; accumulated final files were not recomputed. No PostgreSQL access/mutation, HTTP call, adapter/crawler change, panel change, ESOL, selection or scheduler action occurred.

Files added: `universal_supplier/new_group_proposals.py`, `tests/test_new_group_proposals.py`, `docs/NEW_GROUP_PROPOSALS.md`.
