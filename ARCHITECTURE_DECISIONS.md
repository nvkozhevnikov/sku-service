# Architecture decisions

## ADR-001 — Universal tables, adapter-specific ingestion

Accepted. New suppliers use adapters and configuration, not copied product tables. JSONB retains source-specific facts without changing the canonical contract.

## ADR-002 — Separate source card, canonical product and offer

Accepted. A supplier card may map to an optional cross-supplier canonical product and one or many offers. Partner-ST uses one card → one default offer. This preserves simplicity while supporting future true variants.

## ADR-003 — Conditional external-ID uniqueness

Accepted. `(supplier_id, external_id)` is unique only when `external_id_is_stable=true`. Partner-ST passed full validation and enables it. Adapters without proven stable IDs may keep the field NULL/unstable until audited.

## ADR-004 — Options are raw controls plus explicit relations

Accepted. Option text never creates products. Partner-ST targets resolve to independent cards, so cross-card values become navigation relations; selected self-values remain informational. True variants require proven offer identity.

## ADR-005 — Raw plus normalized values

Accepted. Raw availability, characteristics and option metadata are retained next to normalized columns. Normalization is nullable and reversible.

## ADR-006 — Field-group hashes and sparse history

Accepted. Separate hashes permit cheap fast updates. History records only real changes, not unchanged daily snapshots.

## ADR-007 — Safe deactivation is crawl-gated

Accepted. Misses increase only in healthy full crawls. Individual 404 and global discovery collapse are different events. Out-of-stock is never inactive.

## ADR-008 — No premature XML or proxy implementation

Accepted. `feed_runs` and transport configuration reserve interfaces, but this stage defines no Sterbrust XML, scheduler, server deployment, proxy pool or CAPTCHA behavior.

## ADR-009 — PostgreSQL topology used by Stage 3B

Superseded by ADR-019 in Stage 3D. Earlier Stage 3B evidence correctly records the host PostgreSQL topology used at that time and is retained as historical evidence only.

## ADR-010 — Supplier isolation is a database invariant

Accepted. Supplier-owned entities expose `(id, supplier_id)` candidate keys and all ambiguity-prone edges reference those pairs. Category parents/memberships/paths, product relations, crawl evidence and change history cannot cross supplier boundaries. This duplicates `supplier_id` on dependent rows intentionally: it turns a security and correctness assumption into an immediately enforceable PostgreSQL constraint and enables supplier-leading indexes.

## ADR-011 — Existing Sterbrust cards bootstrap one-to-one

Accepted. Every complete Sterbrust registry card initially owns a separate `catalog_products` row. Similarity can create an `EXISTING_DUPLICATE_CANDIDATE` review record/report but never an automatic merge.

## ADR-012 — Negative identity evidence has veto power

Accepted. Conflicting brand, model, manufacturer article, or identity-defining characteristic blocks automatic matching. Fuzzy name similarity retrieves candidates only and cannot override a conflict.

## ADR-013 — Public sitemap is a blocked fallback, not a full registry

Accepted. A sitemap run can prove discoverable URLs while maintaining zero writes, but it cannot prove inactive coverage or exact Bitrix property codes. Canonical bootstrap therefore requires a read-only API/export snapshot with stable Sterbrust IDs and raw fields.

## ADR-014 — Stage 3B application boundary

Accepted. Generic HTTP transport, domain models, change detection, crawl state and repositories are supplier-independent. Partner-ST sitemap/HTML selectors live only in its adapter. Supplier SKU is a namespaced identifier and one Partner-ST card produces exactly one default offer; option navigation never produces variants.

## ADR-015 — Historical Stage 3B integration gate

Historical. Stage 3B required an external PostgreSQL integration run. Stage 3D replaces that runtime topology; the historical reports are not rewritten.

## ADR-016 — Stage 3B repairs use the existing schema

Accepted. No migration 011 is required. Crawl audit, URL history, missing/deactivation state and entity history already have dedicated tables. The only absent comparison slot, the options/navigation hash, is stored as internal `_stage3b` metadata in existing `source_products.raw_data`; options remain normalized in their existing tables.

## ADR-017 — Integration evidence is durable but secret-free

Accepted. Only the integration runner reads DB credentials. A successful run records timestamp, server version, non-superuser gate, migrations, integrity assertions and row counts without connection secrets. Static package QA trusts only a complete PASS evidence contract and cannot downgrade it merely because a reviewer has no DB environment.

## ADR-018 — Psycopg reads cannot own write durability

Accepted. Repository connections use `autocommit=True`; explicit repository transaction blocks own atomic writes. A PASS decision requires closing the original connection and independently verifying all durable state through a newly opened connection. SQL files executed through Psycopg must contain only PostgreSQL SQL, never psql client commands.

## ADR-019 — Docker PostgreSQL is the canonical runtime

Accepted and mandatory from Stage 3D. PostgreSQL uses the pinned `postgres:17.11` image, the named `universal_supplier_pgdata` volume and a healthcheck. The bootstrap role is separate from the `NOSUPERUSER NOCREATEDB NOCREATEROLE` application role. The default host port is bound only to `127.0.0.1`; containers use the internal Docker network and `DB_HOST=postgres`. Local/internal traffic uses `DB_SSLMODE=disable` because the stock local container has no TLS certificate. A future remote production topology must configure and verify TLS explicitly.

## ADR-020 — Persistence and backup are distinct controls

Accepted. `docker compose down` and container recreation must retain the named volume. No normal helper removes volumes. Host-visible custom-format `pg_dump` files are stored under `backups/`, excluded from source packages, and must be proven by restoring into a temporary database without touching the primary database.
