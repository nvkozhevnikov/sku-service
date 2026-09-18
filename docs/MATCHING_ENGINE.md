# Matching engine

The engine returns the unchanged business statuses `EXACT_MATCH`, `HIGH_CONFIDENCE_MATCH`, `REVIEW`, `NEW_CANDIDATE`, or `CONFLICT`. Evidence carries provenance, warnings, and data-quality flags separately. Existing links are evaluated first. A known supplier plus an exact value in its confirmed supplier-specific property is evaluated next. Reliable global/manufacturer identifiers, multi-source model evidence, title model tokens, the lower-trust Sterbrust `MODEL` property, category, characteristics, and negative evidence follow. Fuzzy name similarity retrieves candidates only and never auto-accepts.

Stage 3A.2 deterministically reviewed all 12 former sample conflicts. Eleven were reclassified as field-data or extraction warnings and one (`MR-X3` pointing to an `MR-X4` product with a disagreeing general article) remains a hard identity conflict. The complete evidence is in `PARTNER_ST_CONFLICT_REVIEW.csv`; final counts are in `STAGE3A2_SUMMARY.txt`.

Strong brand, corroborated title/model, manufacturer article, or key-characteristic differences veto automatic matching. A stale standalone MODEL property becomes `STERBRUST_MODEL_PROPERTY_STALE` when exact supplier article, compatible brand, title model, and supporting article evidence agree. `BS-912G` and `BS-912GC` remain distinct. Prices and availability stay on supplier offers and never define variants.

`NEW_CANDIDATE` is allowed only after existing mapping, confirmed supplier article, global/manufacturer identifier, safe brand+model, and other strong candidate paths produce no acceptable candidate. An exact supplier article never creates a new Sterbrust product.

One supplier article on several Sterbrust products remains `REVIEW` regardless of which candidate looks better. Stage 3A.2 performs no merge, product creation, ingest, or write operation.

## Stage 3A.3 persistent validation

Initial supplier-article matching and `existing_link` revalidation now call the same identity-quality classifier. A confirmed existing link has highest precedence, but it is revalidated against supplier-specific article, brand, manufacturer article, title/model evidence, and normalized identity-critical properties. Field-data and extraction warnings do not overturn a stable mapping; strong evidence of another identity does.

The real PP-800F/508002 fixture remains `EXACT_MATCH` on RUN1 and becomes a validated `EXISTING_LINK` on RUN2 while retaining `STERBRUST_MODEL_PROPERTY_STALE`. The changed MR-X3 to MR-X4 fixture remains `CONFLICT / IDENTITY_CONFLICT` when the old supplier article no longer confirms the mapping and title, model, and article contradict it.

Physical properties use a typed normalization layer. Voltage, power, dimensions, diameter, weight, capacity, and execution tokens retain raw values and also expose Decimal canonical values or normalized text. Comparisons record conversion, configurable per-property tolerance and importance. Formatting/unit variations and supporting-property warnings are not hard conflicts; an identity-critical mismatch can block the match.

Supplier-specific article evidence is accepted only from `candidate.supplier_identifiers[source.supplier_code]`. The legacy scalar `candidate.supplier_article` is not a namespaced identity source and is excluded from all exact/high-confidence paths. Manufacturer/global article matching remains a separate path.
# Semantic identity hardening (Stage 4 correction)

`BRAND_MODEL` is automatic only when the model belongs to the source product,
significant adjacent execution qualifiers agree, and no identity contradiction
is present. Qualifiers such as `NEW`, `PRO`, `PLUS`, `MAX`, and other narrowly
allow-listed edition markers are preserved in model identity. A model following
an accessory-head plus a compatibility marker (for example, an accessory
"for" a machine) is treated as a reference model, not the accessory's identity.

Existing mappings are revalidated using `product_matches` lineage. Failed prior
automatic mappings are quarantined; manual or human-confirmed mappings are not
silently converted to automatic mappings or removed. Supplier-specific article
identity remains stronger than weak brand+model evidence.
