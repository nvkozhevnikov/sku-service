# Sterbrust REST API field map

Snapshot date: 2026-09-01. Source: live Sterbrust Bitrix REST, iblock 48.

| Logical field | Actual REST field / property ID | Property code | Source location | Type | Coverage |
|---|---|---|---|---|---:|
| Sterbrust product ID | `id` | — | product top level | integer | 59,427 / 59,427 |
| Name | `name` | — | product top level | string | 59,427 / 59,427 |
| Article | `property1090`, fallback `property1272` | `ARTNUMBER`, `CML2_ARTICLE` | product property | string | 22,849 / 59,427 |
| Legacy supplier article convenience field | `property1273` | `Artikul_postavshika` | product property | string, inactive | 0 / 59,427 |
| Supplier-specific articles | dynamically discovered populated properties | exact prefix `ARTIKUL_` | product properties + live property definitions | one-to-many strings | 30,420 values on 29,085 products |
| Brand ID | `property1140.value` | `BRAND` | element-link property | integer/string | included in brand coverage |
| Brand name | iblock-45 product name resolved from `property1140.value`; fallback manufacturer enum | `BRAND` | linked element | string | 46,308 / 59,427 |
| Manufacturer ID | `property1131.value` | `CML2_MANUFACTURER` | list property | enum ID | 13,628 / 59,427 |
| Manufacturer name | `property1131.valueEnum` | `CML2_MANUFACTURER` | list property | string | 13,628 / 59,427 |
| Model | `property1156.value` | `MODEL` | product property | string | 22,512 / 59,427 |
| Primary section ID | `iblockSectionId` | — | product top level | integer | see field coverage CSV |
| All section IDs | `iblockSection` | — | product top level | array | preserved in registry JSONL |
| Section name/path | section `id/name/iblockSectionId` | — | `catalog.section.list` | reconstructed tree | see field coverage CSV |
| Active/status | `active` | — | product top level | `Y`/`N` | 59,427 / 59,427 |
| Product URL | section `code` + product `code` | — | derived from two REST fields | URL | see field coverage CSV |
| Properties | every populated `property<ID>` | definition supplies `id/code/name/propertyType` | product + property schema | dynamic | 919 definitions available |
| Raw record | all non-empty fields returned by bulk `select[]=*` plus explicit identity fields | — | product response | JSON object | 59,427 / 59,427 |

The API exposes no separate universal unit field for product properties. `unit` remains empty unless a concise unit suffix is explicitly present in the property definition name; the original property name and raw value are always preserved.

Pagination QA: `start=0`, `next=50`, `total=59427`; 1,189 pages in the full bulk pass. A second 1,189-page compact pass recovered properties 1272/1273 omitted by wildcard. `API_REPORTED_TOTAL = UNIQUE_FETCHED_PRODUCT_IDS = 59427`.

Stage 3A.1 found 62 exact-prefix `ARTIKUL_` definitions. `property1546 / ARTIKUL_PARTNER` is mapped to `partner_st` with content evidence from the fixed 50-row Partner-ST sample. General article `property1090 / ARTNUMBER` remains a separate identifier namespace and is never interpreted as a supplier-specific article.

Stage 3A.2 assigns lower trust to the standalone `MODEL` property than to a confirmed supplier article plus compatible brand and title model token. Raw values remain unchanged; normalization warnings and catalog-wide issues are emitted to `STERBRUST_IDENTITY_DATA_QUALITY.csv`.
