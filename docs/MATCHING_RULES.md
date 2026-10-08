# Matching rules, version stage3a3-1.0

## V2.1 operator policy — 2026-10-08

OPERATOR-CONFIRMED: exact full execution + compatible brand/manufacturer and
sold-product kind, no explicit contradiction, can be Existing without N specs.
Missing specs/different counts do not block. Characteristics remain important
corroborating, disambiguation and same-role material contradiction evidence.
Own exact variant in a canonical series title is allowed; true parent is not.
Scoped IDENTITY_ALIAS is distinct from retrieval-only SEARCH_SYNONYM. Significant
+/PRO/NEW/V suffixes survive; accessory/component cannot match complete machine.
Current implementation: universal_supplier/matching_policy_v2.py, adopted in KAMI
only; no silent rerun of predecessor decisions or arbitrary fuzzy acceptance.
See ../ACCEPTANCE_MATRIX.md and ../FALSE_MATCH_CASES.md. Legacy rules below remain
historical runtime context and do not impose a universal characteristic quota.

## Positive evidence

- Existing stable supplier link: exact, unless conflicting identity evidence appears.
- Manufacturer/global identifier + compatible brand + unique candidate: exact/high confidence.
- Known source supplier + exact value in that supplier's confirmed `ARTIKUL_*` property + unique candidate + no material identity conflict: exact match via `SUPPLIER_ARTICLE_EXACT`.
- The same supplier-scoped article on multiple Sterbrust products: `REVIEW` via `DUPLICATE_SUPPLIER_ARTICLE`; nothing is merged.
- An exact supplier-scoped article with a confirmed identity conflict across title/model/general article: `CONFLICT`; nothing is created or auto-accepted.
- A unique exact supplier article with compatible brand and a title that confirms the source model may remain `EXACT_MATCH` when only the Sterbrust `MODEL` property disagrees. The discrepancy is recorded in `warnings` and `data_quality_flags` as `FIELD_DATA_CONFLICT`.
- Brand + model exact after conservative normalization + unique candidate: high confidence.
- Category and key characteristics: supporting evidence only.
- Name similarity: candidate retrieval only, never auto match.

## Negative evidence

Different non-empty brand, corroborated title/model identity, manufacturer article, voltage, dimensions, power, or execution blocks automatic matching. A lone Sterbrust `MODEL` property mismatch is not absolute truth. Suffix letters remain significant: `BS-912G != BS-912GC`; numeric execution tokens such as `2500` and `3200` are preserved.

## Precedence and namespaces

The cascade is: existing confirmed source mapping, confirmed supplier-specific article, manufacturer/global candidates, model confirmed by multiple sources, title model token, Sterbrust `MODEL` property, then fuzzy/category retrieval evidence. `property1090 / ARTNUMBER` and supplier-specific `ARTIKUL_*` values are kept in different namespaces. A supplier article is never global and never matches another supplier's property merely because the text is equal.

Conflict provenance is recorded as `IDENTITY_CONFLICT`, `FIELD_DATA_CONFLICT`, or `EXTRACTION_CONFLICT`. These are evidence classifications, not new business statuses.

## Normalization

Normalization applies Unicode NFKC, lowercase, `ё -> е`, whitespace/dash normalization, contextual multiplication-sign normalization, decimal comma handling, approved brand aliases, and removal of presentation separators from a model. In model context only, a confirmed leading brand is removed and visual Latin/Cyrillic confusables are canonicalized deterministically. Significant tokens such as `NEW`, `C`, `S`, `Servo`, `CNC`, `F3`, `2500`, or `3200` are preserved.

Examples: `STALEX BS-912G` and `Stalex BS 912 G` normalize to brand `stalex`, model `bs912g`. `BS-912GC` remains `bs912gc` and cannot match automatically to `bs912g`.

`Partner M3080A` with confirmed brand `Partner` normalizes to `m3080a`. `M3080B` and mixed-script `M3080В` normalize to the same model with a data-quality warning. `LX13` and `LX13 NEW` remain distinct strings; compatibility is evaluated only with exact supplier article plus title/general-article evidence and produces an extraction warning.

## Persistent link revalidation

The same evidence-quality classifier is used for a first match, a confirmed supplier-article match, and an existing-link check. Existing mapping is evaluated first. A model-property disagreement already classified as `FIELD_DATA_CONFLICT` or `EXTRACTION_CONFLICT` stays a warning when unchanged corroborating evidence remains. It cannot produce `EXACT_MATCH -> CONFLICT` by itself.

An existing link is blocked when current strong evidence indicates another identity, including incompatible brand, changed corroborated title/model plus loss of the old supplier article, incompatible manufacturer article, or an identity-critical normalized-property conflict. The result is `CONFLICT`, class `IDENTITY_CONFLICT`, and `auto_accepted = false`.

## Typed property comparison

Default canonical units are V for voltage, W for power, mm for dimensions/diameter, kg for weight, and L for capacity. Decimal commas and points are equivalent. Supported conversions include W/kW, V/В, mm/cm/m, g/kg/t, and mL/L/m3. Decimal arithmetic is used; floating point is not used.

Property comparisons return `PROPERTY_IDENTITY_CONFLICT`, `PROPERTY_FORMAT_VARIATION`, `PROPERTY_UNIT_VARIATION`, `PROPERTY_WITHIN_TOLERANCE`, or `PROPERTY_DATA_QUALITY_WARNING`. Default importance is identity-critical for voltage, power, dimensions, diameter, capacity, and execution, supporting for weight, and non-identity for unknown properties. Category-specific policy overlays may replace importance and absolute/relative tolerances without changing raw evidence.

## Supplier namespace isolation correction

Supplier articles can enter strong matching only through `supplier_identifiers[source.supplier_code]`. Equal legacy scalar values on `source.supplier_article` and `candidate.supplier_article` are ignored as positive evidence because the candidate scalar does not prove a namespace. Consequently, `ARTIKUL_PARTNER = 100` and `ARTIKUL_KEDR = 100` neither match nor merge products. This restriction does not affect the separately typed manufacturer/global identifier path.
# Stage 4 semantic guardrails

- Fuzzy name similarity never auto-matches.
- `BRAND_MODEL` never auto-matches a reference/compatibility model.
- Different significant execution qualifiers are different identities.
- Weak brand+model evidence cannot override a supplier-specific article.
- Multiple active default offers from distinct source products of one supplier
  require explicit equivalence; otherwise selection is `REVIEW`.
# Execution-aware model suffixes

Model identity preserves both recognized execution words (`NEW`, `PRO`, and the
configured word set) and an immediately adjacent compact suffix of one or two
original uppercase Latin letters. Thus `TU 2304 V`, `TU2304 V`, and `TU2304V`
normalize to `tu2304v`, which remains distinct from `TU2304` (`tu2304`). The
generic suffix rule excludes common unit tokens and never consumes long or
Cyrillic prose, so `PP-800F Универсальный станок` remains `pp800f`.

This is conservative identity evidence, not fuzzy matching. A base-model-only
candidate cannot be auto-accepted when the source carries a preserved execution
suffix.
