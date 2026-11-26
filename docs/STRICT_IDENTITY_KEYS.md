# Strict identity keys: rules and RC evaluation

## Rules

| Value | Legacy key (retrieval, persisted, IDs) | Strict comparison (identity decisions) |
|---|---|---|
| Model `BF-1.5` vs `BF 15` | equal (`bf15`) | **different** (`bf1.5` / `bf15`) |
| Model `BF-1,5` vs `BF 1.5` | equal | equal |
| Model `JWL-1015+` vs `JWL-1015` | equal | **different** (semantic plus, ANTIPATTERNS #2) |
| Article `303 4252` vs `3034252` | equal | equal: one side is unformatted |
| Article `710-1150` vs `710 1150` | equal | equal: same digit groups |
| Article `10-100` vs `101-00` | equal | **different**: both formatted, different groups |

Functions in `sterbrust_matching/normalization.py`:

- `normalize_model` is the legacy retrieval key. It is stored in the database and used by indexes, `new_candidate_id` and `new_group_id`. Its output must not change.
- `normalize_model_strict` is the model key for identity comparisons.
- `article_lookup_key` is the article retrieval key, with digit-group boundaries removed.
- `articles_compatible(a, b)` decides whether two articles are the same identifier.

The core matcher uses the strict comparisons. These modules also use them for identity decisions: `new_group_proposals`, `beka_visible_identity`, `beka_operator_decisions`, `kami_matching` (source proof), `kami_overlap`, `offline_review_resolution`, `intervesp_enrichment`, `characteristic_evidence`, `proposed_matching` and the review screen model indicators. One-off historical scripts under `scripts/` were not changed.

## F01/F03 local compatibility design (2026-10-19)

OPERATOR-CONFIRMED scope: isolated feature
`feature/sterbrust-matching-f01-f03-20261019`, base
`58152e4bd390361d3e97ee475bae78a91295e241`. No apply or ID migration.

INFERENCE (design recorded before implementation): keep the public Product
dataclass, its dict[str, Any] properties field, legacy model normalizers and
ModelRoleEvidence unchanged. A separate own-title-model helper will select the
raw span corresponding to the existing ownership decision; retrieval still uses
normalize_model. Reference tails and raw_data fallback are not new authority.

For properties, keep scalar strings for single values. Only multiple distinct
values become deterministic tuples of strings in the matching view. This fits
the existing Any value annotation, but is an intentional value-contract extension:
review the consumers before enabling it. The two card constructors, registry
loaders and canonical/generic property comparisons must share expansion rules;
typed scalar normalization still receives one value at a time. Original cards,
raw labels, units and positions are not rewritten. Canonical alias groups retain
all values; uncertainty in a critical multivalue group blocks automatic identity,
while non-identity multiplicity does not. No new property-role heuristics.

Required gate: RED/GREEN for both constructors, fake persisted loader, simultaneous
base/full candidates, source/target duplicates, synonyms/units/order, existing
manual authority and legacy ID fixtures. F02, R04, R05 and UI search remain out of
scope. No claim of LIVE impact or applied mappings follows from these tests.

REPOSITORY-VERIFIED implementation: `own_model_for_identity` selects a raw owned
title span in both `matching_service._source` and `identity_service.source_product`.
Attached composite plus and decimal comma are recovered when the legacy tokenizer
stopped within the same token. Unknown layouts yield an empty model, not a guessed
alias. No raw_data brand/kind/model fallback was added (F02 remains separate).
Legacy `classify_model_role`, `normalize_model`, `normalize_model_strict`, retrieval
indexes and NEWC/NEWG recipes are unchanged.

`identity_properties` is shared by source constructors and the two registry
property loaders. `property_values` expands scalar/tuple/list values before typed
comparison. Canonical alias comparison checks every pair, not just the first;
ambiguous critical groups block auto even when the other card lacks that property.
Explicit 220/380 sets remain uncertain, rather than choosing one execution.
Equivalent explicit units are retained without appending duplicate unit aliases.
Recognized attached units (1.5kW) are not given a second inferred unit. Ordinary
scalar-map first-value rendering remains compatible. This extends critical
ambiguity handling beyond a kind profile; it does not impose a positive spec quota.
Property roles and aliases themselves were not expanded.

REPOSITORY-VERIFIED regression entry: `tests/test_f01_f03_identity_inputs.py`
(80 cases). Both constructors and the real SELECT loader run on synthetic in-memory
rows; no persistence/runtime reconciliation is invoked. Existing manual decisions,
namespaces, policy V2.1/V2.2 and legacy technical-ID vectors have separate controls.
Report output outside this repository records exact RED/GREEN logs and feature HEAD.
Historical one-off scripts retain their own converters and were not repaired or
run; do not infer universal script coverage from the shared runtime fix.

INFERENCE limits: unknown layouts can increase REVIEW; raw capture provenance and
unmodelled property-role ambiguity need separate investigation. Real affected
rows, live registry coverage, SQL persistence and production compatibility have
not been verified. Local code verification does not authorize recalculating links.

## Evaluating the effect on RC (read-only)

`scripts/evaluate_strict_identity_keys.py` loads cards, the Sterbrust registry and current links. It then runs the normal matcher twice:

1. with the strict keys temporarily replaced by the legacy key;
2. with the current code.

The script reports every card whose decision differs. The connection uses `default_transaction_read_only=on`, and the script stops if PostgreSQL does not confirm read-only mode. The output directory must not exist yet, so earlier evidence is never overwritten.

Windows PowerShell, with the usual RC environment:

```powershell
$env:DB_HOST='127.0.0.1'; $env:DB_PORT='55451'; $env:DB_NAME='universal_supplier_kami_rc'
$env:DB_USER='<read-only or normal RC user>'; $env:DB_PASSWORD='<from runtime.json>'; $env:DB_SSLMODE='disable'
python scripts/evaluate_strict_identity_keys.py --out reports/STRICT_KEYS_EVALUATION_<date>
```

### Output

The output directory contains three files:

- `SUMMARY.json`: counts per pass (`with_existing_links` and `fresh`), per supplier and per transition, plus the patterns found in the changed cards.
- `CHANGED_with_existing_links.csv`: changed cards for the `with_existing_links` pass.
- `CHANGED_fresh.csv`: changed cards for the `fresh` pass.

The `with_existing_links` pass shows what the next normal reconcile would decide. The `fresh` pass matches without any current link.

Each changed card has one of these transitions:

| Transition | Meaning | Action |
|---|---|---|
| `AUTO_TO_NOT_AUTO` | An automatic link becomes REVIEW or CONFLICT. | Expected for `MODEL_DECIMAL_POINT` and `MODEL_PLUS`. Check a sample by hand. |
| `NOT_AUTO_TO_AUTO` | A card that needed review now links automatically. | Not expected. Investigate every row. |
| `CANDIDATE_CHANGED` | A different candidate is proposed. | Investigate every row. |
| `STATUS_OR_METHOD_CHANGED` | The decision is the same, but the evidence path differs. | This is usually `ARTICLE_DIGIT_GROUPS`, where a lookalike article no longer counts as exact article proof. |

### Decision guide

1. `AUTO_TO_NOT_AUTO` rows with `MODEL_DECIMAL_POINT` or `MODEL_PLUS` should be real execution differences. If a sample shows formatting noise instead, record the exact pattern before the rule is changed.
2. Every `ARTICLE_DIGIT_GROUPS` row means both articles were formatted differently. Confirm such rows on the supplier and Sterbrust cards.
3. Running this script changes nothing in RC. Applying new decisions still requires the normal authorized lifecycle gates in WORKING_RULES.md.
