# Stage 3E Optimum component mapping

| Old Optimum component | Decision | Universal Supplier target and reason |
|---|---|---|
| `discovery.py` dynamic sitemap index plus catalog pagination | REUSE | Keep inside `universal_supplier.adapters.optimum_source`; no Optimum URLs or selectors enter generic core. |
| `parser.py` product/category classification and HTML selectors | ADAPT | Preserve proven semantics, but expose Bitrix element ID for every card and use it before canonical URL as source identity. SKU becomes optional supplier-scoped article evidence, never source identity. |
| `category_tree.py` taxonomy merge and merchandising exclusions | REUSE | Adapter-owned taxonomy logic remains isolated and retains the 44-node historical QA behavior. |
| `normalize.py` URL, price, availability and unit normalization | REUSE | Preserve raw values next to normalized values; `Цена по запросу` remains a NULL price. |
| Pydantic standalone source models | ADAPT | Retain as strict parser boundary, then convert to generic `ProductCard`; no duplicate canonical/database model. |
| Standalone `HttpFetcher` | REUSE | Read-only HTTP transport remains adapter-local with bounded concurrency and no Playwright/proxy. |
| Standalone `OptimumAdapter._deduplicate` | ADAPT | Deduplicate by Bitrix-ID/canonical identity and export duplicate live URLs into generic `source_product_urls` with `url_kind=alias`. |
| Related/accessory links | ADAPT | Represent as navigation relations only; they never become sellable variants or extra offers. |
| One parsed product card | ADAPT | One Optimum `source_product` and one Optimum default `offer`; commercial data stays supplier-specific. |
| Standalone ZIP/report exporter | DROP | Stage 3E owns reports and self-contained packaging; no nested parser release architecture. |
| Standalone CLI | ADAPT | Replace with supplier-selectable universal runner supporting `optimum`, preserving the existing Partner-ST runner. |
| Historical JSONL/CSV reports | REUSE AS REFERENCE | Use for regression/completeness comparison only; RUN1/RUN2 closure evidence must come from fresh HTTP and real Docker PostgreSQL. |
| Standalone tests | SELECTIVE REUSE | Port source-semantic fixtures/tests; do not import parser-only architecture assumptions that conflict with universal persistence/matching. |
| Separate database/schema/matching engine | DROP | Optimum uses the existing suppliers/source_products/offers/details/matching tables and the existing conservative matching precedence. |

No destructive refactor is authorized. Migrations 001–011 remain immutable, and Stage 3E will first attempt integration without migration 012.

