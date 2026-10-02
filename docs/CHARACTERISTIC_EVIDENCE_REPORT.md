# Offline typed characteristic / section evidence — 2026-10-01

## DONE / APPLIED / VERIFIED

- DONE: isolated evidence learner, typed unit normalization, advisory NEW/REVIEW evaluator and readiness model.
- APPLIED: new local module/script/tests and a separate evidence bundle. No integration into the live matcher, exporter, panel or database.
- VERIFIED (REPOSITORY-VERIFIED / PACKAGED-EVIDENCE): 148 tests + 8 subtests passed; saved pair replay exactly equals the current module output; bundle SHA-256 checks passed.
- Not LIVE-VERIFIED: no HTTP, SQL, production write or real readiness transition was performed. Existing/Conflict results and original matching artifacts are unchanged.

## Actual evidence, not historical NEW/REVIEW

Inputs are the current `reports/RC_LOCAL/MATCHING_FINAL.json`, its pinned saved registry, matching-manifest RUN2 evidence, canonical JSONL characteristics and section tree. Only `EXISTING_CONFIRMED` with `full_model_confirmed=true` contributes training examples.

119 source records (61 Intervesp / 58 Beka-Mak RU), 78 unique canonical products. No confirmed Beka-Mak TR training pairs are available in this artifact. All observed training scopes are bandsaw. Every supplier capture checksum, source identity/URL and paired canonical snapshot hash was checked. The absence index covers all 59,500 registry IDs, including inactive products; it is absence **support**, not proof of NEW.

Final bundle: `reports/RC_LOCAL/CHARACTERISTIC_EVIDENCE_2026-10-01_VERIFIED/`.
Earlier sibling bundles `...2026-10-01/` and `...2026-10-01_FINAL/` are superseded development evidence, retained without deletion; do not consume them.

| Evidence | Total | SAFE | REVIEW | INSUFFICIENT |
| --- | ---: | ---: | ---: | ---: |
| Scoped supplier-property → canonical-property mappings | 78 | 22 | 50 | 6 |
| Source-category → SECTION mappings | 9 | 3 | 4 | 2 |

These are context-specific mappings, not 78 global synonyms. SAFE requires at least three distinct canonical examples and complete agreement. Multiple supplier offers for one canonical product do not increase distinct-example support. A value difference/unknown parse makes the characteristic mapping REVIEW, not a repaired matcher decision. Unmapped labels remain UNCLASSIFIED.

SAFE category evidence:

| Supplier / source category | SECTION_ID | Unique confirmed examples | Agreement |
| --- | ---: | ---: | ---: |
| Beka-Mak RU / Ручные ленточнопильные станки | 4005 | 6 | 1.0 |
| Intervesp / Колонные автоматические ленточнопильные станки | 4074 | 7 | 1.0 |
| Intervesp / Колонные полуавтоматические ленточнопильные станки | 4037 | 13 | 1.0 |

Conflicting sections remain REVIEW, even with a strong majority. Insufficient, ambiguous, cyclic, missing or inactive section ancestry cannot become SAFE. The JSON map includes actual section paths, all conflicting section IDs, ratios and paired examples.

## Typed properties and guards

- IDENTITY_CRITICAL: blade length/width/thickness, main motor power, blade speed, perpendicular and **signed** angular cutting capacity, construction/automation type, voltage and category-specific geometry.
- SUPPORTING: overall dimensions/axes, mass, table height, pump power, cutting diameter with unspecified angle.
- NON_IDENTITY: manufacturer display field, country, warranty; these do not override the separately required compatible brand identity.
- Preserve raw values/units beside Decimal-normalized values. `mm/мм`, `m→mm`, `kW/кВт→W`, decimal comma and ordered 2–3-axis dimensions are supported. Missing/unknown units and ambiguous values remain unparsed, never fabricated zero.
- Pump power is not main motor power. Unspecified cutting angle is not perpendicular capacity; unsigned 45° is not an alias of either +45° or −45°. Source 0° frame swing and canonical 90° perpendicular cut are an explicit bandsaw-only convention.
- Semantic guards run before readiness: execution/suffix, own vs reference model, accessory vs machine, equipment scope, brand and upstream conflicts. TU2304V/TU2304, LX20 Pro/LX20 NEW, HCV125 jaws/vise and WP/base regressions remain blocked.

Most useful properties for the **observed bandsaw pairs** are blade length/width, main motor power, perpendicular round capacity and construction type. Their SAFE support is scoped to the relevant supplier/category. Overall dimensions and mass provide supporting checks. Blade thickness/automation remain required for complete readiness even where current paired evidence cannot learn a SAFE alias.

Lathe (centers/spindle bore/power/voltage), vise (jaw width/clamping range) and grinder (wheel dimensions/power/voltage) profiles are **synthetic seed contracts only**. There are no learned SAFE mappings for these types in this bundle; do not apply them globally or claim training coverage.

## Advisory evaluator / readiness

`evaluate_candidate(candidate, evidence, registry_index=..., reference=..., new_group=...)` returns:

- CHARACTERISTIC_IDENTITY_SUPPORT: typed comparisons and missing required properties.
- CHARACTERISTIC_CONTRADICTION: hard semantic/critical-property reasons and source/reference evidence.
- SECTION_EVIDENCE: exact-context map and SAFE verdict, not an inferred section from majority.
- CANONICAL_ABSENCE_SUPPORT: exact/family candidates, snapshot hash and support/verified distinction.

Readiness proposal gates are sequential:

`NEW_CANDIDATE → NEW_GROUPED → CANONICAL_ABSENCE_VERIFIED → SECTION_VERIFIED → IDENTITY_EVIDENCE_COMPLETE → READY_TO_CREATE`

A group needs the separate proven-equivalence proposal, membership and retained NEW_CANDIDATE_ID. Absence verification additionally needs an independent reviewed-absence reference bound to this registry hash; an empty model lookup alone is insufficient. All critical required properties, own identity/provenance and safety guards must pass. `enable_ready_proposal=False` is the default and stops before READY_TO_CREATE. Even opt-in emits an advisory object only, never mutates the supplied row. It is exercised on synthetic fixtures only. **Real READY_TO_CREATE transitions: 0.**

## Integration after Grok evidence

1. Verify bundle SHA-256 and source/registry pins; validate Grok listing/detail characteristics with provenance.
2. Preserve current matcher Existing/Conflict decisions. For NEW/REVIEW only, supply strict cross-source groups from `new_group_proposals`, source characteristics and appropriate comparison evidence to the evaluator.
3. Unseen source/category/property names require new confirmed paired evidence; no fallback global alias or section majority.
4. Review canonical absence independently against the complete registry, including inactive items and plausible family candidates. Section evidence never compensates for an identity contradiction.
5. Store advisory evaluation separately from matching. Only a subsequently authorized integration may use verified gates for grouped proposed-create outputs. No Sterbrust Product ID is generated here; Stage 6 remains BLOCKED_IMPORT_CONTRACT_EVIDENCE.

Files added: `universal_supplier/characteristic_evidence.py`, `scripts/build_characteristic_evidence.py`, `tests/test_characteristic_evidence.py`, this report, and the separate local JSON bundle.

Offline suite: `uv run --offline --with-requirements requirements.txt --with pytest python -m pytest -q tests/test_characteristic_evidence.py tests/test_new_group_proposals.py tests/test_proposed_matching.py tests/test_matching.py tests/test_optimum_identity_review.py`.
Result: **148 passed, 8 subtests passed**, including 25 evidence-layer tests and four parametrized semantic cases. Initial host Python lacked lxml; the final run used existing offline-cached dependencies. Temporary-fixture permission failures were rerun with scoped test permission; no test baselines were substituted.

Bundle files: paired raw characteristics; scoped mappings/inventory; section map; canonical absence index; build counts/input/output SHA-256 manifest. Builder refuses to overwrite an existing output directory. No adapters, crawler, DB, panel, scheduler or original matching files changed in this task.
