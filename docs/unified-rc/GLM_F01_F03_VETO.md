# GLM F01/F03 VETO — targeted independent code review (2026-10-19)

Reviewer: GLM (independent pass, read-only). Scope strictly per `14_GLM_NOW.md`:
exact local patch `58152e4bd390361d3e97ee475bae78a91295e241..0c0164a5ea49659a895866766731bf83adddea76`,
branch `feature/sterbrust-matching-f01-f03-20261019`, worktree
`D:/documents/personal/ChatGPT/Стербруст/work/sku-service-matching-f01-f03-20261019`.
No edits to the branch/worktrees; no fetch/push/DB/HTTP. Probes and this report live only in
`tmp/STERBRUST_GLM_F01_F03_REVIEW_20261019/`.

## Verdict

**No BLOCKING defects found.** 2 CONDITIONAL + 4 OPTIONAL findings below.
**Recommendation: Codex MAY incorporate the patch into the unified feature**, subject to the two
CONDITIONAL items (pre-integration evidence, not code changes).

## 1. Repository-verified scope (task 1) — [VERIFIED]

- `merge-base --is-ancestor 58152e4..0c0164a` → exit 0; history = exactly 3 commits
  (`2251d4a` fix + 2 test-whitespace commits). Worktree `git status --porcelain` clean before and
  after review (pytest caches are gitignored).
- Diff scope = exactly 9 paths, matching the handoff: `sterbrust_matching/matching.py` (+34/-8),
  `sterbrust_matching/product_identity.py` (+113), `universal_supplier/identity_service.py`,
  `universal_supplier/matching_service.py`, new `tests/test_f01_f03_identity_inputs.py` (302),
  4 markdown docs. `sterbrust_matching/normalization.py`,
  `property_normalization.py`, `proposal_export`, `new_group_proposals` and all other protected
  modules are NOT in the diff → prior developer changes and legacy keys preserved by construction.
- Last 2 commits touch only 2 deleted lines of the new test file (whitespace), confirming the handoff.

## 2. F01 — raw own execution in both constructors (task 2) — [VERIFIED, no false auto]

`own_model_for_identity` (`sterbrust_matching/product_identity.py:302-328`) returns a raw title
span only when its `normalize_model` equals the pre-existing `classify_model_role` decision;
reference-role cards truncate at the compatibility marker and return `""` when no own model exists.
Independently reproduced (probe `probes.py`, outputs in §7):

- `"Станок токарный TEST 45/500S+"` → `TEST 45/500S+`; strict key `45500s+` ≠ base `45500s`
  → base candidate gets model conflict → no auto. Same for `50/650S+`, `BF-1.5`/`BF-1,5`
  (strict `bf1.5` vs `bf15`), `6K+2`, `45S+PRO`, `ATC1+C`.
- `"Ручка для станка TEST BD-7+"` / `"Контроллер для TEST BD-7+"` → `""` (no own model);
  probe confirms empty model yields `REVIEW/NAME_CANDIDATE_ONLY`, never auto.
- Legacy retrieval keys unchanged and golden vectors green:
  `normalize_model("45/500S+")=="45500s"`, NEWC/NEWG ids reproduced exactly
  (`test_legacy_keys_and_technical_id_golden_vectors`).
- Both constructors covered: `matching_service._source` (card/loader path) and
  `identity_service.source_product` (reference/accessory path) both call the new function;
  `registry_product` keeps raw `model_raw`. Retrieval (`_candidates`) still indexes by legacy
  `normalize_model`, so base+full candidates are retrieved together (test asserts `{"101","102"}`).

## 3. F03 — multivalue critical properties (task 3) — [VERIFIED, no false auto]

- `identity_properties` (`product_identity.py:338-370`): single value → str; multiple distinct →
  sorted tuple; equal duplicates collapse; empty values never erase an observed value; unit column
  appended only when not already equivalent. Order-independence verified by probe (reversed rows →
  identical dict) and by `test_f03_representation_is_order_independent_and_lossless`.
- 220/380: both orders, missing units, unit-in-value vs unit-column, single `"220/380 В"` values,
  target-side list/tuple duplicates and loader `value_flat` duplicates all block auto
  (`BAD_GROUPS` matrix × permutation × both constructors × reverse, `test_f03_voltage_set...`,
  `test_f03_target_loader_retains_all_values_and_units`,
  `test_f03_existing_list_target_and_unknown_opposite_stay_safe`).
- Non-identity duplicates (`Цвет`) and equal-after-unit-conversion duplicates stay auto-TRUE
  (`test_f03_equal_or_nonidentity_duplicates_do_not_blanket_block`) — no blanket N-rows block.
- All `Product.properties` consumers audited (`git grep`): `matching.py:73-89` (via
  `property_values`), `matching.py:149-165` (`compare_category_identity` canonical layer + voltage
  text), `new_group_proposals._product` (builds its own scalar maps, untouched by patch loaders),
  `characteristic_evidence` (passes `properties={}`). Tuple values that reach
  `mains_voltage_band` text render as a Python repr but still parse to `mixed`
  (probe: `'mixed'`), and conflicts/evidence remain `json.dumps`-safe (probe: OK).
- Historical decisions: legacy keys/recipes/manual links re-verified by
  `test_manual_canonical_id_is_not_replaced` (EXISTING_LINK_MANUAL authority unchanged),
  `test_operator_rejection_and_postponed_contract_unchanged`,
  `test_supplier_namespace_never_changes_with_input_view`.

## 4. Legacy keys, manual decisions, coverage (task 4) — [VERIFIED with gaps]

- `normalize_model`/`normalize_model_strict` byte-identical (module untouched). Article policy and
  supplier-scoped namespaces unchanged. NEWC/NEWG golden vectors unchanged.
- Historical SKIPs: 94 skips on this checkout are missing-local-evidence / opt-in DB / PowerShell
  guards (`tests/conftest.py:75`); the 2 handoff Beka replays are among them and are still absent.
- Full local suite (Python 3.14): **1635 passed, 94 skipped, 70 subtests passed, 0 failed**
  (superset of the handoff's 414-count because this checkout carries more evidence files).
  New file alone: 80 passed in 0.57s.

## 5. Findings

### F-1 [CONDITIONAL] Strictness flip will retroactively convert some previously accepted autos
- **Code:** `sterbrust_matching/product_identity.py` `_ambiguous` (≈456-476) +
  `compare_category_identity` critical-ambiguity branch (528-536) +
  `matching.py:_property_identity_quality` (76-84). Registry rows that previously collapsed
  duplicate critical properties via dict last-wins, or carried a single `"220/380 В"` value that
  compared compatible via `compatible_voltage_set`, now deterministically produce
  `PROPERTY_AMBIGUOUS_DUPLICATE_VALUES`/`VOLTAGE_BAND_MISMATCH` → REVIEW/CONFLICT. Same for
  `EXISTING_LINK` revalidation in `identity_service.classify_identity_card:166`.
- **Deterministic proof:** `tests/test_f01_f03_identity_inputs.py::test_f03_voltage_set_does_not_choose_one_execution`
  (`value="220/380 В"`, `target="220/380 В"` → FALSE, previously compatible TRUE) and
  `test_f03_existing_list_target_and_unknown_opposite_stay_safe`.
- **Why CONDITIONAL, not BLOCKING:** this is the explicit F03 contract (FALSE_MATCH_CASES.md;
  "critical ambiguity blocks auto deterministically", "target-side duplicates"). But it *will*
  churn accepted decisions on the first real reconcile.
- **Minimal fix boundary:** none in code; required evidence before unified-feature integration =
  replay of the 2 historical Beka cases + a dry-run count of currently-auto rows that flip to
  REVIEW/CONFLICT, so operators can budget the manual queue.

### F-2 [CONDITIONAL] Platform/coverage gaps remain open
- **Code:** n/a (environment). Python 3.12 and Linux untested here (3.14 only, `py -0p` shows a
  single 3.14 interpreter); 2 historical Beka replays absent; SQL concurrency/persistence not run.
- **Deterministic test:** run `py -3.12 -m pytest tests` / Linux CI — currently not executable in
  this sandbox (no 3.12 interpreter).
- **Minimal fix boundary:** none in this patch; gate the unified-feature merge on an approved
  3.12/Linux run + replay evidence (matches handoff BLOCKED list).

### F-3 [OPTIONAL] Tuple repr leaks into voltage-band text construction
- **Code:** `sterbrust_matching/matching.py:153-158` — `f"{key} {value}"` renders
  `('220 V', '380 V')` as Python source repr. Verified harmless today (`mains_voltage_band` still
  yields `mixed`; probe §7), but it couples the guard to `tuple.__repr__` formatting.
- **Deterministic test:** `mains_voltage_band("x Напряжение " + str(("220 V","380 V"))) == "mixed"` (passes now).
- **Minimal fix:** `" ".join((key, *property_values(value)))` instead of the f-string (behavior
  unchanged, repr dependency removed).

### F-4 [OPTIONAL] Arbitrary unit text appended to non-identity string values
- **Code:** `sterbrust_matching/product_identity.py:364-367`. Probe: rows
  `("Цвет","красный","для станка BD-7")` → `"красный для станка BD-7"`. Cannot create a false auto
  (NON_IDENTITY ⇒ warnings only) but pollutes evidence/audit text.
- **Minimal fix:** append the unit only when `policy.property_type` is a typed unit kind
  (`in _TYPE_UNITS`) or `importance != NON_IDENTITY`.

### F-5 [OPTIONAL] Docstring contradicts implementation in `identity_properties`
- **Code:** `product_identity.py:341-342` — "raw cards retain row positions and repeated identical
  observations independently"; the implementation (344-370) sorts keys/values and dedupes.
  Cosmetic; fix the docstring (one line).

### F-6 [OPTIONAL] Criticality gate duplicated across two policy vocabularies
- **Code:** `matching.py:76` uses `resolve_policy(raw_key)` (English defaults only), while Russian
  aliases are critical only via the canonical layer
  (`product_identity.compare_category_identity`, `PROPERTY_POLICIES[canonical]`). Probe confirmed
  `prop_conflicts == {}` for raw `"Напряжение"` duplicates while the canonical layer blocked.
  Correct today because every critical alias maps in `PROPERTY_ALIASES`; a future alias added to
  only one vocabulary could silently lose the intra-side ambiguity block.
- **Minimal fix:** in `_property_identity_quality`, resolve policy as
  `resolve_policy(canonical_property_name(key) or key)` (mirroring `identity_properties:352-353`),
  plus an invariant test that every IDENTITY_CRITICAL key in `PROPERTY_POLICIES` is reachable
  from `resolve_policy` after canonicalization.

## 6. Recommendation

**Codex may incorporate the patch into the unified feature.** No blocking defect; negative-case
coverage is genuine (order permutations, both constructors, loader/registry sides, stale-proposal
rejection, manual-link precedence). Conditions before merge: F-1 replay/churn evidence and F-2
3.12/Linux run; apply F-3..F-6 either now (cheap) or as immediate follow-ups in the unified
feature. Keep separate handling of semantic-role uncertainty vs strict technical-ID keys as done.

## 7. Evidence artifacts (same directory)

- `raw_diff.txt` — exact full diff base→head.
- `code_diff.txt` — code-file hunks only.
- `probes.py` — deterministic probe script (no repo writes); key outputs:
  - `own_model_for_identity('TEST 45/500S+')` → raw span kept; `BF-1,5` → strict `bf1.5`;
    accessory/reference titles → `""`; empty model → `REVIEW`, never auto.
  - tuple voltage properties → band `mixed`; conflicts/evidence `json.dumps` OK.
  - `identity_properties` reversed rows → identical dict; unit-garbage append case (F-4).
- Test receipts: `tests/test_f01_f03_identity_inputs.py` → 80 passed; full `tests/` on Python 3.14 →
  1635 passed, 94 skipped, 70 subtests passed.

DONE / APPLIED (report+probes in new tmp dir only) / VERIFIED (§1-§4) / BLOCKED (F-2 evidence
gaps). Next action for operator: authorize 3.12/Linux run + historical Beka replay, then merge.
**STOP.**
