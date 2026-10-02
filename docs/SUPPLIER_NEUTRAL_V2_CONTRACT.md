# Universal Supplier neutral contract 2.0

`REPOSITORY-VERIFIED`. Root `UniversalSupplier`, contract
`universal-supplier-neutral/2.0`. UTF-8 XML, deterministic supplier-scoped order.
This is a frozen proposal/data interchange contract, **not** an ESOL/Bitrix
mapping or an executable import instruction. `import_authorized=false` and every
product `actionable=false`. A future write adapter needs separate authorization.

## Classes and keys

* `EXISTING/Product`: accepted source record and real positive
  `sterbrust_product_id`. Multiple suppliers may share that canonical ID. Release
  has 506 source records / 458 unique canonical IDs, not 506 canonical products.
* `NEW/Product`: exactly two distinct `new_group_id`, stable source
  `new_candidate_id`. There is no Sterbrust Product ID or proposed canonical ID.
  `FullCard` carries the complete accepted saved-source payload.
* `UNRESOLVED/Product`: REVIEW or CONFLICT, candidate and blocking evidence only.
  Candidate IDs in JSON evidence are proposed retrieval targets, never assigned
  identity. These records cannot produce updates or creates.

`namespace + external_id` is the source key. `source_ref` is deterministic SHA-256
of that pair. The root reconciliation and input hash envelopes pin the accepted
state. A decoder must reject unknown contract versions/classifications, missing
source keys and duplicates. Counts reconcile to 4407 = 506 + 2 + 3640 + 259.

## Typed envelopes

Named XML elements marked `encoding=json` contain UTF-8 JSON text (ordinary XML
escaping applies). JSON preserves typed arrays, field states, original labels,
units, power/weight roles, provenance and nested optional configuration without
guessing a target importer schema. `null=true` means JSON null, never zero or an
empty article. Elements are decoded with XML parsing then JSON parsing.
Unknown keys may be retained; deleting provenance on import is not compliant.

`FullCard` fields have `state`, `value`, `provenance`, `reason`. States are
OBSERVED / NOT_FOUND / UNKNOWN / CONFLICT. Mandatory identity/content must be
OBSERVED; UNKNOWN/CONFLICT cannot pass FULL readiness. NOT_FOUND documents,
manufacturer/legal name or articles are explicit absence, not invented values.
Own gallery originals are URLs, not proof of downloaded image binaries.

* Price request/missing/ambiguous has numeric price null. Numeric public price
  must be positive, finite; it is not purchase, RRP or Sterbrust sale price.
* Availability and quantity are independent. Stock is not inferred from price.
  Saved observation timestamps are not LIVE stock verification. No quantity is
  fabricated. The two NEW preorder values are labeled source metadata.
* `RS`, execution/suffix, product kind and accessory guards remain unchanged.
* Optional HTZ conveyor geometry is in `optional_configuration`, not base machine
  dimensions. Pulley width is not blade width. Unspecified UYYM weight is not net
  or gross. Continuous/max/S6 and range/option values stay separate.

`CommercialObservation.selected_comparable_public_view` is a deterministic
**proposed** view only: accepted Existing, known in_stock, dated evidence, finite
positive price and currency. Compare only within canonical ID/currency/public
role, lowest price then newest observation then scoped key. It neither activates
disabled suppliers nor replaces persisted catalog_offer_selection (121 rows in
the backup). DB activation/freshness/offer-selection policy remains authoritative
for any future operational write; this view is not an approved sale-price update.

## Companion files

Six literal UTF-8 machine CSVs: EXISTING.csv, OFFERS.csv, NEW_FULL_CARDS.csv,
REVIEW.csv, CONFLICT.csv, SOURCE_TRACE.csv. Blank numeric CSV cells mean null.
Treat strings as data, never execute formulas when importing CSV into Excel.
QA workbook is frozen, with filters/panes and a reconciled formula summary.
Over-32767-character evidence is split into explicitly numbered workbook columns
without truncation; CSV/XML and PRODUCT_CARD_PAYLOADS.json retain original text.
Long JSON evidence is inspectable in the formula bar, not a model/identity source.

The independent canonical correction packs are retained; duplicate146998/147023
must not arbitrarily select a survivor. Data-error cases are not automatic NEW.
SHA256_MANIFEST.json lists byte size and checksum of deliverables. The database
and the accepted filesystem decisions are separate deliverables: restoring the
dump alone does not apply accepted proposal links to SQL.
