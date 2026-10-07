from __future__ import annotations
from universal_supplier.effective_identity import effective_manual_mapping_sql,accepted_current_mapping_sql

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sterbrust_matching.normalization import normalize_brand, normalize_model, normalize_text, normalized_name
from sterbrust_matching.decision_history import decision_fingerprint, warning_fingerprint
from sterbrust_matching.product_identity import PROPERTY_POLICIES, canonical_property_name
from sterbrust_matching.property_normalization import DEFAULT_PROPERTY_POLICIES

from .change_detection import card_group_values, card_hashes
from .commercial import ReadOnlySupplierProduct
from .commercial_persistence import HttpCapture, observation_from_snapshot, product_card_from_snapshot
from .models import ProductCard
from .repository import IngestCounts
from .state import DiscoveryHealth
from .stage6e import (
    AnomalyThresholds, MissingPolicy, QualityProfile,
    deactivation_was_suppressed, detect_anomalies,
)

PARTNER_CODE = "partner_st"
SUPPLIER_CONFIGS = {
    "partner_st": ("Partner-ST", "https://partner-st.ru/", "partner_st"),
    "optimum": ("Optimum", "https://optimum.su/", "optimum"),
    # Configuration only.  No supplier is inserted until an explicitly
    # authorised repository call invokes ensure_supplier().
    "intervesp": ("Intervesp", "https://intervesp.ru/", "intervesp"),
    "beka_mak": ("Beka-Mak", "https://beka-mak.su/", "beka_mak"),
    "beka_mak_tr": ("Bekamak manufacturer reference", "https://www.bekamak.com/", "beka_mak_tr"),
    "kami": ("КАМИ", "https://www.stanki.ru/", "kami"),
}
PASSIVE_COMMERCIAL_SUPPLIERS = frozenset({"intervesp", "beka_mak", "beka_mak_tr", "kami"})


def should_quarantine_automatic_link(previous_auto_accepted: bool, new_status: str) -> bool:
    return bool(previous_auto_accepted and new_status in {"CONFLICT", "REVIEW", "NEW_CANDIDATE"})


def decision_data_quality_flags(decision: dict, evidence: dict) -> list[str]:
    flags = evidence.get("data_quality_flags", decision.get("data_quality_flags", []))
    if isinstance(flags, str):
        try:
            flags = json.loads(flags)
        except json.JSONDecodeError:
            flags = []
    return sorted(set(map(str, flags))) if isinstance(flags, list) else []


@dataclass(frozen=True)
class PostgresConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str | None
    sslmode: str

    @classmethod
    def from_env(cls) -> "PostgresConfig":
        required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_SSLMODE")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError("Missing PostgreSQL settings: " + ", ".join(missing))
        return cls(os.environ["DB_HOST"], int(os.environ["DB_PORT"]), os.environ["DB_NAME"],
                   os.environ["DB_USER"], os.environ["DB_PASSWORD"], os.environ["DB_SSLMODE"])

    def kwargs(self) -> dict:
        values = {"host": self.host, "port": self.port, "dbname": self.dbname, "user": self.user,
                  "sslmode": self.sslmode}
        if self.password:
            values["password"] = self.password
        return values


class PostgresRepository:
    """PostgreSQL repository for Docker or host-local client workflows."""

    def __init__(self, config: PostgresConfig) -> None:
        import psycopg
        # Plain SELECT statements must never open an implicit outer transaction
        # that can absorb later connection.transaction() blocks as savepoints.
        self.connection = psycopg.connect(**config.kwargs(), autocommit=True)

    def gate(self) -> dict:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT 1, current_setting('server_version'), rolsuper FROM pg_roles WHERE rolname = current_user")
            one, version, superuser = cursor.fetchone()
        if one != 1:
            raise RuntimeError("SELECT 1 failed")
        if superuser:
            raise RuntimeError("Stage 3B runtime/migration role must not be superuser")
        return {"select_1": True, "server_version": version, "runtime_role_superuser": False}

    def close(self) -> None:
        self.connection.close()

    def ensure_supplier(self, supplier_code: str = PARTNER_CODE) -> int:
        """Create one isolated supplier namespace without supplier-specific tables."""
        if supplier_code not in SUPPLIER_CONFIGS:
            raise ValueError(f"Unknown supplier code: {supplier_code}")
        name, base_url, adapter_name = SUPPLIER_CONFIGS[supplier_code]
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO suppliers (code, name, base_url, adapter_name, enabled, transport_mode,
                           deactivate_after_misses, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,'direct',3,now(),now())
                   ON CONFLICT (code) DO UPDATE SET updated_at = now()
                   RETURNING id""",
                (supplier_code, name, base_url, adapter_name, supplier_code not in PASSIVE_COMMERCIAL_SUPPLIERS),
            )
            supplier_id = cursor.fetchone()[0]
            cursor.execute(
                """UPDATE sterbrust_product_supplier_identifiers
                   SET supplier_id=%s, updated_at=now()
                   WHERE supplier_code=%s AND supplier_id IS DISTINCT FROM %s""",
                (supplier_id, supplier_code, supplier_id),
            )
        return supplier_id

    def ensure_partner_supplier(self) -> int:
        """Backward-compatible Stage 3B helper."""
        return self.ensure_supplier(PARTNER_CODE)

    @staticmethod
    def _ensure_passive_commercial_supplier(cursor, supplier_code: str) -> int:
        """Create only the isolated namespace; never touch Sterbrust identifiers."""
        name, base_url, adapter_name = SUPPLIER_CONFIGS[supplier_code]
        if supplier_code == 'kami':
            # Exact KAMI capture replay must not consume the supplier sequence.
            cursor.execute('SELECT id FROM suppliers WHERE code=%s FOR UPDATE', (supplier_code,))
            existing = cursor.fetchone()
            if existing:
                return existing[0]
        cursor.execute(
            """INSERT INTO suppliers (code, name, base_url, adapter_name, enabled, transport_mode,
                       deactivate_after_misses, created_at, updated_at)
               VALUES (%s,%s,%s,%s,false,'direct',3,now(),now())
               ON CONFLICT (code) DO NOTHING
               RETURNING id""",
            (supplier_code, name, base_url, adapter_name),
        )
        inserted = cursor.fetchone()
        if inserted:
            return inserted[0]
        cursor.execute("SELECT id FROM suppliers WHERE code=%s FOR UPDATE", (supplier_code,))
        existing = cursor.fetchone()
        if existing is None:
            raise RuntimeError("passive commercial supplier disappeared during creation")
        return existing[0]

    def persist_commercial_observation(self, product: ReadOnlySupplierProduct, capture: HttpCapture) -> dict[str, Any]:
        """Persist one captured public-price observation after explicit DB authorisation.

        The method is intentionally inert until called by a future, separately
        authorised isolated-DB workflow.  It does not invoke matching,
        reconciliation, offer selection, a scheduler, or any external service.
        """
        card = product_card_from_snapshot(product, capture)
        observation = observation_from_snapshot(product, capture)
        return self._persist_passive_observation(card, observation, capture)

    def persist_kami_observation(self, card: ProductCard, capture: HttpCapture) -> dict[str, Any]:
        """KAMI-only explicit writer; caller must preflight the new isolated target."""
        from .kami_persistence import prepare_observation
        card, observation = prepare_observation(card, capture)
        return self._persist_passive_observation(card, observation, capture)

    def materialize_kami_saved_content(self, card: ProductCard, capture: HttpCapture) -> bool:
        """Explicit offline content re-extraction; does not fabricate a new GET.

        Only an already persisted exact KAMI capture may be used. Commercial
        values/timestamps, source identity, links and activation stay untouched.
        The ordinary observation replay remains an exact no-op.
        """
        from .kami_persistence import prepare_observation
        card, _ = prepare_observation(card, capture)
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("""SELECT p.id,p.raw_data,p.catalog_product_id,o.id
                FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
                JOIN offers o ON o.source_product_id=p.id AND o.offer_kind='default'
                WHERE s.code='kami' AND p.external_id=%s AND p.external_id_is_stable
                FOR UPDATE OF p,o""", (card.external_id,))
            row = cursor.fetchone()
            if row is None or row[2] is not None:
                raise RuntimeError('Saved-content materialization requires unlinked existing KAMI source')
            product_id, previous, _, offer_id = row
            cursor.execute("""SELECT c.supplier_id FROM supplier_http_captures c
                WHERE c.source_product_id=%s AND c.capture_fingerprint=%s""",
                (product_id, capture.fingerprint))
            saved = cursor.fetchone()
            if saved is None:
                raise RuntimeError('Exact persisted capture required; no synthetic observation')
            prior_capture = (previous or {}).get('commercial_capture', {})
            if prior_capture.get('response_sha256') != capture.response_sha256:
                raise RuntimeError('Cannot overwrite newer content with historical capture')
            if (previous or {}).get('_kami_content_fact_hash') == card.raw_data['_kami_content_fact_hash']:
                return False
            # Only derived content fields are replaced, never source identity or
            # commercial projection. Extraction provenance is independently kept.
            updated = dict(previous or {})
            for key in ('source_content', 'property_evidence', 'comparison_matrix',
                        'variant_evidence', 'description_state', 'documents_state',
                        'documents_provenance', '_kami_content_fact_hash'):
                if key in card.raw_data:
                    updated[key] = card.raw_data[key]
            updated['saved_content_extraction'] = {
                'adapter': 'kami-v1', 'response_sha256': capture.response_sha256,
                'evidence_ref': capture.evidence_ref, 'no_new_HTTP_observation': True}
            cursor.execute("""UPDATE source_products SET brand_raw=%s,manufacturer_raw=%s,
                description_text=%s,description_html=%s,raw_data=%s::jsonb WHERE id=%s""",
                (card.brand or None, card.manufacturer or None, card.description_text,
                 card.description_html, json.dumps(updated, ensure_ascii=False), product_id))
            self._replace_details(cursor, saved[0], product_id, offer_id, card, capture.observed_at.isoformat())
            return True

    def _persist_passive_observation(self, card, observation, capture) -> dict[str, Any]:
        observed_at = capture.observed_at.isoformat()
        if card.supplier_code not in PASSIVE_COMMERCIAL_SUPPLIERS:
            raise ValueError("commercial persistence is limited to passive suppliers")
        region = capture.region_code or ""
        counts = IngestCounts()
        with self.connection.transaction(), self.connection.cursor() as cursor:
            supplier_id = self._ensure_passive_commercial_supplier(cursor, card.supplier_code)
            if card.supplier_code == "intervesp":
                from .intervesp_listing import persistence_identity
                resolved = persistence_identity(cursor, supplier_id, card)
                if resolved != card.external_id:
                    card = replace(card, external_id=resolved)
                    observation = replace(observation, external_id=resolved)
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           (f"commercial:{supplier_id}:{card.external_id}",))
            cursor.execute(
                """SELECT id, catalog_product_id, last_success_at, raw_data
                   FROM source_products WHERE supplier_id=%s AND external_id=%s AND external_id_is_stable
                   FOR UPDATE""",
                (supplier_id, card.external_id),
            )
            existing = cursor.fetchone()
            kami_content_changed = True
            kami_commercial_changed = True
            if existing:
                source_product_id, catalog_product_id, last_success_at, raw_data = existing
                if card.supplier_code == 'kami':
                    kami_content_changed = (raw_data or {}).get('_kami_content_fact_hash') != card.raw_data.get('_kami_content_fact_hash')
                    kami_commercial_changed = (raw_data or {}).get('_kami_commercial_fact_hash') != card.raw_data.get('_kami_commercial_fact_hash')
                cursor.execute("SELECT id FROM supplier_http_captures WHERE source_product_id=%s AND capture_fingerprint=%s",
                               (source_product_id, capture.fingerprint))
                exact_capture = cursor.fetchone()
                if exact_capture:
                    return {"ingest_counts": counts, "source_product_id": source_product_id,
                            "capture_id": exact_capture[0], "observation_id": None,
                            "observation_created": False, "projection_updated": False, "exact_noop": True}
                if catalog_product_id is not None:
                    raise RuntimeError("commercial persistence refuses a catalog-linked source product")
                projection_region = str((raw_data or {}).get("_commercial_projection_region", ""))
                projection_updated = projection_region == region and (last_success_at is None or capture.observed_at >= last_success_at)
                if projection_updated:
                    if capture.capture_scope == 'intervesp_listing':
                        from .intervesp_listing import retain_detail_content
                        next_raw = retain_detail_content(card.raw_data, raw_data or {})
                    else:
                        next_raw = dict(card.raw_data)
                    next_raw["_commercial_projection_region"] = region
                    cursor.execute(
                        """UPDATE source_products SET sku=%s,name=%s,source_url=%s,canonical_url=%s,raw_data=%s::jsonb,
                           last_seen_at=%s,last_success_at=%s,
                           last_changed_at=CASE WHEN %s THEN %s ELSE last_changed_at END,active=true,missed_crawls=0,
                           last_http_status=%s,updated_at=now() WHERE id=%s""",
                        (card.sku, card.name, card.requested_url, card.canonical_url,
                         json.dumps(next_raw, ensure_ascii=False), observed_at, observed_at,
                         card.supplier_code != 'kami' or kami_content_changed, observed_at,
                         card.http_status, source_product_id),
                    )
                    counts.changed_products += int(card.supplier_code != 'kami' or kami_content_changed)
            else:
                projection_updated = True
                next_raw = dict(card.raw_data)
                next_raw["_commercial_projection_region"] = region
                cursor.execute(
                    """INSERT INTO source_products
                       (supplier_id,external_id,external_id_is_stable,sku,name,source_url,canonical_url,raw_data,
                        first_seen_at,last_seen_at,last_success_at,last_changed_at,active,missed_crawls,last_http_status)
                       VALUES (%s,%s,true,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,true,0,%s) RETURNING id""",
                    (supplier_id, card.external_id, card.sku, card.name, card.requested_url, card.canonical_url,
                     json.dumps(next_raw, ensure_ascii=False), observed_at, observed_at, observed_at, observed_at,
                     card.http_status),
                )
                source_product_id = cursor.fetchone()[0]
                counts.new_products += 1
            cursor.execute("SELECT id FROM offers WHERE source_product_id=%s AND offer_kind='default' FOR UPDATE", (source_product_id,))
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """INSERT INTO offers
                       (supplier_id,source_product_id,external_offer_id,external_id_is_stable,offer_kind,sku,title,
                        price,old_price,currency,availability_raw,availability_normalized,quantity,raw_data,
                        first_seen_at,last_seen_at,last_success_at,last_changed_at,active,missed_crawls)
                       VALUES (%s,%s,%s,true,'default',%s,%s,%s,%s,%s,%s,%s,NULL,%s::jsonb,%s,%s,%s,%s,false,0)
                       RETURNING id""",
                    (supplier_id, source_product_id, card.external_id, card.sku, card.name, card.price, card.old_price,
                     card.currency or None, card.availability_raw, card.availability_normalized,
                     json.dumps(card.raw_data, ensure_ascii=False), observed_at, observed_at, observed_at, observed_at),
                )
                offer_id = cursor.fetchone()[0]
                counts.new_offers += 1
            else:
                offer_id = row[0]
                if projection_updated:
                    cursor.execute(
                        """UPDATE offers SET sku=%s,title=%s,price=%s,old_price=%s,currency=%s,
                           availability_raw=%s,availability_normalized=%s,quantity=NULL,raw_data=%s::jsonb,
                           last_seen_at=%s,last_success_at=%s,
                           last_changed_at=CASE WHEN %s THEN %s ELSE last_changed_at END,
                           active=false,missed_crawls=0,updated_at=now()
                           WHERE id=%s""",
                        (card.sku, card.name, card.price, card.old_price, card.currency or None,
                         card.availability_raw, card.availability_normalized, json.dumps(card.raw_data, ensure_ascii=False),
                         observed_at, observed_at, card.supplier_code != 'kami' or kami_commercial_changed,
                         observed_at, offer_id),
                    )
                    counts.changed_offers += int(card.supplier_code != 'kami' or kami_commercial_changed)
            if card.supplier_code == "kami" and projection_updated and kami_content_changed:
                # KAMI content is visible to existing product/panel queries;
                # other supplier projections and their accepted state are untouched.
                cursor.execute(
                    """UPDATE source_products SET brand_raw=%s, manufacturer_raw=%s,
                       description_text=%s, description_html=%s WHERE id=%s AND supplier_id=%s""",
                    (card.brand or None, card.manufacturer or None, card.description_text,
                     card.description_html, source_product_id, supplier_id),
                )
                self._replace_details(cursor, supplier_id, source_product_id, offer_id, card, observed_at)
            cursor.execute(
                """INSERT INTO supplier_http_captures
                   (supplier_id,source_product_id,requested_url,final_url,http_status,content_type,observed_at,
                    capture_fingerprint,response_sha256,evidence_sha256,evidence_ref,redirect_urls,region_code,region_label,diagnostics)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb) RETURNING id""",
                (supplier_id, source_product_id, capture.requested_url, capture.final_url, capture.http_status,
                 capture.content_type, observed_at, capture.fingerprint, capture.response_sha256, capture.evidence_sha256,
                 capture.evidence_ref, json.dumps(capture.redirects), region, capture.region_label,
                 json.dumps({"codes": capture.diagnostics, "capture_scope": capture.capture_scope,
                             "response_hash_basis": capture.response_hash_basis})),
            )
            capture_id = cursor.fetchone()[0]
            cursor.execute(
                """INSERT INTO offer_commercial_observations
                   (supplier_id,source_product_id,offer_id,capture_id,observed_at,extraction_fingerprint,price_state,
                    price,old_price,currency,availability_raw,availability_normalized,price_type,price_raw,price_source,
                    extraction_evidence,unusable_price_reasons,region_code)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'unknown',%s,%s,%s::jsonb,%s::jsonb,%s) RETURNING id""",
                (supplier_id, source_product_id, offer_id, capture_id, observed_at, observation.extraction_fingerprint,
                 observation.price_state.value, observation.price, observation.old_price, observation.currency,
                 observation.availability_raw, observation.availability_normalized, observation.price_raw,
                 observation.price_source, json.dumps(observation.extraction_evidence, ensure_ascii=False),
                 json.dumps(observation.unusable_price_reasons, ensure_ascii=False), region),
            )
            observation_id = cursor.fetchone()[0]
        return {"ingest_counts": counts, "source_product_id": source_product_id, "offer_id": offer_id,
                "capture_id": capture_id, "observation_id": observation_id, "observation_created": True,
                "projection_updated": projection_updated, "exact_noop": False}

    def start_crawl_run(self, *, started_at: str, baseline_discovered_count: int | None,
                        metadata: dict[str, Any] | None = None,
                        supplier_code: str = PARTNER_CODE) -> int:
        supplier_id = self.ensure_supplier(supplier_code)
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO crawl_runs
                   (supplier_id, crawl_type, started_at, status, baseline_discovered_count,
                    anomalous_discovery, deactivation_allowed, metadata)
                   VALUES (%s,'full',%s,'running',%s,false,false,%s::jsonb) RETURNING id""",
                (supplier_id, started_at, baseline_discovered_count,
                 json.dumps(metadata or {}, ensure_ascii=False)),
            )
            return cursor.fetchone()[0]

    def record_crawl_error(self, crawl_run_id: int, *, url: str, error_type: str,
                           message: str, http_status: int | None = None,
                           details: dict[str, Any] | None = None) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SELECT supplier_id FROM crawl_runs WHERE id=%s", (crawl_run_id,))
            run = cursor.fetchone()
            if run is None:
                raise RuntimeError(f"Unknown crawl_run_id={crawl_run_id}")
            supplier_id = run[0]
            cursor.execute(
                """INSERT INTO crawl_errors
                   (crawl_run_id, supplier_id, url, error_type, http_status, message, details)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (crawl_run_id, supplier_id, url, error_type, http_status, message,
                 json.dumps(details or {}, ensure_ascii=False)),
            )
            cursor.execute(
                """INSERT INTO crawl_run_items
                   (crawl_run_id, supplier_id, discovered_url, item_status, http_status,
                    error_type, raw_metadata)
                   VALUES (%s,%s,%s,'error',%s,%s,%s::jsonb)
                   ON CONFLICT (crawl_run_id, discovered_url) DO UPDATE SET
                    item_status='error', http_status=EXCLUDED.http_status,
                    error_type=EXCLUDED.error_type, processed_at=now(),
                    raw_metadata=EXCLUDED.raw_metadata""",
                (crawl_run_id, supplier_id, url, http_status, error_type,
                 json.dumps(details or {}, ensure_ascii=False)),
            )

    def fail_crawl_run(self, crawl_run_id: int, *, finished_at: str, message: str) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE crawl_runs SET finished_at=%s, status='failed',
                   error_count=(SELECT count(*) FROM crawl_errors WHERE crawl_run_id=%s),
                   metadata=metadata || %s::jsonb WHERE id=%s""",
                (finished_at, crawl_run_id, json.dumps({"failure": message}), crawl_run_id),
            )

    def update_crawl_progress(self, crawl_run_id: int, progress: dict[str, Any]) -> None:
        """Merge a sanitized progress snapshot without changing crawl status."""
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE crawl_runs
                   SET metadata=jsonb_set(coalesce(metadata,'{}'::jsonb),'{progress}',%s::jsonb,true)
                   WHERE id=%s""",
                (json.dumps(progress, ensure_ascii=False), crawl_run_id),
            )

    def ingest(self, cards: list[ProductCard], observed_at: str, *, crawl_run_id: int | None = None,
               discovery_health: DiscoveryHealth | None = None, discovered_urls: list[str] | None = None,
               error_count: int = 0, supplier_code: str | None = None,
               schema_signals: list[str] | None = None,
               crawl_settings: dict[str, Any] | None = None) -> IngestCounts:
        counts = IngestCounts()
        card_codes = {card.supplier_code for card in cards}
        if supplier_code is None:
            if len(card_codes) != 1:
                raise ValueError("ingest requires exactly one supplier_code")
            supplier_code = next(iter(card_codes))
        if card_codes and card_codes != {supplier_code}:
            raise ValueError("cross-supplier card batch is forbidden")
        with self.connection.transaction(), self.connection.cursor() as cursor:
            supplier_id = self.ensure_supplier(supplier_code)
            url_to_product: dict[str, int] = {}
            observed_ids: set[int] = set()
            for card in cards:
                hashes = card_hashes(card)
                values = card_group_values(card)
                cursor.execute(
                    """SELECT id, identity_hash, description_hash, properties_hash, images_hash,
                              documents_hash, category_hash, raw_data
                       FROM source_products
                       WHERE supplier_id=%s AND external_id=%s AND external_id_is_stable""",
                    (supplier_id, card.external_id),
                )
                previous = cursor.fetchone()
                previous_meta = (previous[7] or {}).get("_stage3b", {}) if previous else {}
                old_hashes = dict(zip(
                    ("identity", "description", "properties", "images", "documents", "categories"),
                    previous[1:7],
                )) if previous else {}
                old_hashes["options"] = previous_meta.get("options_hash")
                changed_groups = [name for name in ("identity", "description", "properties", "images", "documents", "categories", "options")
                                  if previous is not None and old_hashes.get(name) != hashes[name]]
                product_changed = bool(changed_groups)
                stored_raw_data = dict(card.raw_data or {})
                stored_raw_data["_stage3b"] = {
                    "options_hash": hashes["options"],
                    "group_values": {key: value for key, value in values.items() if key != "commercial"},
                }
                cursor.execute(
                    """INSERT INTO source_products
                       (supplier_id, external_id, external_id_is_stable, sku, name, source_url, canonical_url,
                        brand_raw, manufacturer_raw, description_text, description_html, raw_data,
                        identity_hash, description_hash, properties_hash, images_hash, documents_hash, category_hash,
                        first_seen_at, last_seen_at, last_success_at, last_changed_at, active, missed_crawls, last_http_status)
                       VALUES (%s,%s,true,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,0,%s)
                       ON CONFLICT (supplier_id, external_id) WHERE external_id IS NOT NULL AND external_id_is_stable
                       DO UPDATE SET sku=EXCLUDED.sku, name=EXCLUDED.name, source_url=EXCLUDED.source_url,
                         canonical_url=EXCLUDED.canonical_url, brand_raw=EXCLUDED.brand_raw,
                         manufacturer_raw=EXCLUDED.manufacturer_raw, description_text=EXCLUDED.description_text,
                         description_html=EXCLUDED.description_html, raw_data=EXCLUDED.raw_data,
                         identity_hash=EXCLUDED.identity_hash, description_hash=EXCLUDED.description_hash,
                         properties_hash=EXCLUDED.properties_hash, images_hash=EXCLUDED.images_hash,
                         documents_hash=EXCLUDED.documents_hash, category_hash=EXCLUDED.category_hash,
                         last_seen_at=EXCLUDED.last_seen_at, last_success_at=EXCLUDED.last_success_at,
                         last_changed_at=CASE WHEN source_products.identity_hash IS DISTINCT FROM EXCLUDED.identity_hash
                           OR source_products.description_hash IS DISTINCT FROM EXCLUDED.description_hash
                           OR source_products.properties_hash IS DISTINCT FROM EXCLUDED.properties_hash
                           OR source_products.images_hash IS DISTINCT FROM EXCLUDED.images_hash
                           OR source_products.documents_hash IS DISTINCT FROM EXCLUDED.documents_hash
                           OR source_products.category_hash IS DISTINCT FROM EXCLUDED.category_hash
                           OR source_products.raw_data #>> '{_stage3b,options_hash}'
                              IS DISTINCT FROM EXCLUDED.raw_data #>> '{_stage3b,options_hash}'
                           THEN EXCLUDED.last_changed_at ELSE source_products.last_changed_at END,
                         active=true, missed_crawls=0, last_http_status=EXCLUDED.last_http_status, updated_at=now()
                       RETURNING id""",
                    (supplier_id, card.external_id, card.sku, card.name, card.requested_url, card.canonical_url,
                     card.brand or None, card.manufacturer or None, card.description_text, card.description_html,
                     json.dumps(stored_raw_data, ensure_ascii=False, default=str), hashes["identity"], hashes["description"],
                     hashes["properties"], hashes["images"], hashes["documents"], hashes["categories"],
                     observed_at, observed_at, observed_at, observed_at, card.http_status),
                )
                product_id = cursor.fetchone()[0]
                observed_ids.add(product_id)
                self._record_field_observations(cursor, product_id, card, observed_at)
                self._record_seen_observation(cursor, product_id, crawl_run_id, observed_at)
                url_to_product[card.canonical_url.rstrip("/")] = product_id
                if previous is None:
                    counts.new_products += 1
                elif product_changed:
                    counts.changed_products += 1
                else:
                    counts.unchanged_products += 1
                cursor.execute(
                    "SELECT id, commercial_hash, raw_data FROM offers WHERE source_product_id=%s AND offer_kind='default'", (product_id,)
                )
                previous_offer = cursor.fetchone()
                price_type, price_raw, price_source = self._price_provenance(card)
                offer_raw_data = {"source": f"{supplier_code} default offer",
                                  "_stage3b": {"commercial_value": values["commercial"]}}
                cursor.execute(
                    """INSERT INTO offers
                       (supplier_id, source_product_id, external_offer_id, external_id_is_stable, offer_kind, sku,
                        title, price, old_price, currency, availability_raw, availability_normalized, quantity,
                         price_type, price_raw, price_source, raw_data, commercial_hash, identity_hash,
                         first_seen_at, last_seen_at, last_success_at,
                         last_changed_at, active, missed_crawls)
                        VALUES (%s,%s,%s,true,'default',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,true,0)
                       ON CONFLICT (source_product_id) WHERE offer_kind='default' DO UPDATE SET
                        sku=EXCLUDED.sku, title=EXCLUDED.title, price=EXCLUDED.price, old_price=EXCLUDED.old_price,
                         currency=EXCLUDED.currency, availability_raw=EXCLUDED.availability_raw,
                         availability_normalized=EXCLUDED.availability_normalized, quantity=EXCLUDED.quantity,
                         price_type=EXCLUDED.price_type, price_raw=EXCLUDED.price_raw,
                         price_source=EXCLUDED.price_source,
                        raw_data=EXCLUDED.raw_data, commercial_hash=EXCLUDED.commercial_hash,
                        last_seen_at=EXCLUDED.last_seen_at, last_success_at=EXCLUDED.last_success_at,
                        last_changed_at=CASE WHEN offers.commercial_hash IS DISTINCT FROM EXCLUDED.commercial_hash
                          THEN EXCLUDED.last_changed_at ELSE offers.last_changed_at END, active=true, missed_crawls=0,
                        updated_at=now() RETURNING id""",
                    (supplier_id, product_id, card.external_id, card.sku, card.name, card.price, card.old_price,
                      card.currency or None, card.availability_raw, card.availability_normalized, card.quantity,
                      price_type, price_raw, price_source,
                     json.dumps(offer_raw_data, ensure_ascii=False, default=str), hashes["commercial"], hashes["identity"],
                     observed_at, observed_at, observed_at, observed_at),
                )
                offer_id = cursor.fetchone()[0]
                if previous_offer is None:
                    counts.new_offers += 1
                elif previous_offer[1] != hashes["commercial"]:
                    counts.changed_offers += 1
                else:
                    counts.unchanged_offers += 1
                if crawl_run_id is not None and previous is not None:
                    old_values = previous_meta.get("group_values", {})
                    for changed_group in changed_groups:
                        history_group = "other" if changed_group == "options" else changed_group
                        new_value = values[history_group]
                        old_value = old_values.get(history_group)
                        if old_value != new_value:
                            self._record_history(cursor, crawl_run_id, supplier_id, product_id, None,
                                                 history_group, old_value, new_value, observed_at)
                            counts.change_history_events += 1
                if crawl_run_id is not None and previous_offer is not None and previous_offer[1] != hashes["commercial"]:
                    old_commercial = ((previous_offer[2] or {}).get("_stage3b") or {}).get("commercial_value")
                    self._record_history(cursor, crawl_run_id, supplier_id, None, offer_id,
                                         "commercial", old_commercial, values["commercial"], observed_at)
                    counts.change_history_events += 1
                self._upsert_product_urls(cursor, product_id, card, observed_at)
                if previous is None or product_changed:
                    self._replace_details(cursor, supplier_id, product_id, offer_id, card, observed_at)
                if crawl_run_id is not None:
                    item_status = "fetched_new" if previous is None else ("fetched_changed" if product_changed else "fetched_unchanged")
                    cursor.execute(
                        """INSERT INTO crawl_run_items
                           (crawl_run_id, supplier_id, source_product_id, discovered_url, item_status,
                            http_status, raw_metadata)
                           VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                           ON CONFLICT (crawl_run_id, discovered_url) DO UPDATE SET
                            source_product_id=EXCLUDED.source_product_id, item_status=EXCLUDED.item_status,
                            http_status=EXCLUDED.http_status, error_type=NULL, processed_at=now(),
                            raw_metadata=EXCLUDED.raw_metadata""",
                        (crawl_run_id, supplier_id, product_id, card.requested_url, item_status,
                         card.http_status, json.dumps({"final_url": card.final_url,
                                                       "canonical_url": card.canonical_url}, ensure_ascii=False)),
                    )
            self._upsert_navigation_relations(cursor, supplier_id, cards, url_to_product, observed_at)
            self._bootstrap_canonical_properties(cursor)
            self._reconcile_supplier_properties(cursor, supplier_id)
            self._reconcile_sterbrust_property_evidence(cursor)
            if crawl_run_id is not None:
                discovered_count = len(discovered_urls if discovered_urls is not None else cards)
                cursor.execute("SELECT crawl_settings,base_url FROM suppliers WHERE id=%s", (supplier_id,))
                live_settings, base_url = cursor.fetchone()
                frozen = crawl_settings if crawl_settings is not None else live_settings
                anomaly = self._record_quality_snapshot(
                    cursor, supplier_id, crawl_run_id, cards, discovered_count,
                    schema_signals=schema_signals or [], crawl_settings=frozen, base_url=base_url,
                )
                allow_missing = bool(
                    discovery_health and discovery_health.deactivation_allowed
                    and anomaly.allow_missing_consequences
                )
                missing_policy = MissingPolicy.from_mapping((frozen or {}).get("missing_policy"))
                self._apply_missing(cursor, supplier_id, crawl_run_id, observed_ids, observed_at,
                                    allow_missing, counts,
                                    discovered_urls=set(discovered_urls or ()),
                                    anomaly_guarded=anomaly.critical,
                                    missing_policy=missing_policy)
                health = discovery_health
                status = "partial" if error_count or anomaly.critical else "succeeded"
                suppressed = deactivation_was_suppressed(
                    deactivation_allowed=bool(health.deactivation_allowed) if health else False,
                    anomaly_critical=anomaly.critical,
                )
                cursor.execute(
                    """UPDATE crawl_runs SET finished_at=%s, status=%s, discovered_count=%s,
                       fetched_count=%s, new_count=%s, changed_count=%s, unchanged_count=%s,
                       missing_count=%s, error_count=%s, baseline_discovered_count=%s,
                       discovery_ratio=%s, anomalous_discovery=%s, deactivation_allowed=%s,
                       metadata=metadata || %s::jsonb
                       WHERE id=%s AND supplier_id=%s""",
                    (observed_at, status, discovered_count, len(cards), counts.new_products,
                     counts.changed_products, counts.unchanged_products, counts.missing_products,
                     error_count, health.baseline_discovered_count if health else None,
                     health.discovery_ratio if health else None,
                     health.anomalous_discovery if health else False,
                      allow_missing,
                      json.dumps({"default_offer_counts": {"new": counts.new_offers,
                                                            "changed": counts.changed_offers,
                                                            "unchanged": counts.unchanged_offers},
                                  "change_history_events": counts.change_history_events,
                                  "data_quality": {"anomaly_codes": list(anomaly.codes),
                                                   "critical": anomaly.critical,
                                                   "evidence": anomaly.evidence},
                                  "deactivation_suppressed": suppressed}, ensure_ascii=False),
                     crawl_run_id, supplier_id),
                )
        return counts

    @staticmethod
    def _bootstrap_canonical_properties(cursor) -> None:
        """Bootstrap the registry from the existing Stage 6B taxonomy."""
        policies = dict(DEFAULT_PROPERTY_POLICIES)
        policies.update(PROPERTY_POLICIES)
        display_names = {
            "power": "Мощность", "motor_power": "Мощность двигателя",
            "voltage": "Напряжение", "length": "Длина", "width": "Ширина",
            "height": "Высота", "diameter": "Диаметр", "capacity": "Ёмкость",
            "weight": "Масса", "execution": "Исполнение / версия",
            "max_turning_diameter": "Максимальный диаметр обработки",
            "distance_between_centers": "Расстояние между центрами",
            "spindle_bore": "Диаметр отверстия шпинделя",
            "spindle_taper": "Конус шпинделя", "machine_mode": "Исполнение станка",
            "table_size": "Размер стола", "travel_x": "Ход по оси X",
            "travel_y": "Ход по оси Y", "travel_z": "Ход по оси Z",
            "spindle_speed": "Частота вращения шпинделя",
            "max_drilling_diameter": "Максимальный диаметр сверления",
            "spindle_travel": "Ход шпинделя", "jaw_width": "Ширина губок",
            "clamping_range": "Диапазон зажима", "vise_type": "Тип тисков",
            "thread_range": "Диапазон резьбы", "drive_type": "Тип привода",
            "working_radius": "Рабочий радиус", "tool_range": "Диапазон инструмента",
            "wheel_size": "Размер круга", "physical_size": "Габариты",
        }
        units = {"power": "W", "motor_power": "W", "voltage": "V", "length": "mm",
                 "width": "mm", "height": "mm", "diameter": "mm", "weight": "kg",
                 "capacity": "L", "max_turning_diameter": "mm", "distance_between_centers": "mm",
                 "spindle_bore": "mm", "travel_x": "mm", "travel_y": "mm", "travel_z": "mm",
                 "spindle_speed": "rpm", "max_drilling_diameter": "mm", "spindle_travel": "mm",
                 "jaw_width": "mm", "clamping_range": "mm", "working_radius": "mm"}
        for key, policy in sorted(policies.items()):
            data_type = "range" if policy.property_type in {"range", "thread_range"} else "string" if policy.property_type in {"string", "dimension", "execution"} else "number"
            identity = policy.importance == "IDENTITY_CRITICAL"
            cursor.execute(
                """INSERT INTO canonical_properties
                   (code,display_name_ru,data_type,canonical_unit,description,
                    show_in_characteristics,usable_for_identity,identity_critical,usable_as_filter)
                   VALUES (%s,%s,%s,%s,%s,true,%s,%s,true)
                   ON CONFLICT(code) DO NOTHING""",
                (key.upper(), display_names.get(key, key.replace("_", " ").title()), data_type,
                 units.get(key), f"Stage 6B taxonomy: {key}", identity, identity),
            )

    @staticmethod
    def _reconcile_supplier_properties(cursor, supplier_id: int) -> int:
        cursor.execute(
            """SELECT pp.raw_name,pp.normalized_name,coalesce(pp.raw_unit,''),
                      min(pp.raw_value) AS sample_value,count(*) AS occurrences,
                      coalesce(string_agg(DISTINCT node.raw_name,' / ' ORDER BY node.raw_name),'') AS category_context
               FROM product_properties pp JOIN source_products sp ON sp.id=pp.source_product_id
               LEFT JOIN product_category_paths path ON path.source_product_id=sp.id AND path.is_current
               LEFT JOIN product_category_path_nodes node ON node.path_id=path.id
               WHERE sp.supplier_id=%s
               GROUP BY pp.raw_name,pp.normalized_name,coalesce(pp.raw_unit,''),sp.supplier_id""",
            (supplier_id,),
        )
        rows = list(cursor.fetchall())
        for raw_name, normalized_name, raw_unit, sample, occurrences, category_context in rows:
            canonical_key = canonical_property_name(raw_name)
            cursor.execute("SELECT id,data_type,canonical_unit FROM canonical_properties WHERE code=%s AND active", (canonical_key.upper(),))
            candidate = cursor.fetchone() if canonical_key else None
            inferred_type = "number" if __import__("re").search(r"[-+]?\d+(?:[.,]\d+)?", str(sample or "")) else "string"
            evidence = {"occurrences": occurrences, "discovered_from": "product_properties",
                        "stage6b_taxonomy_key": canonical_key or None}
            cursor.execute(
                """INSERT INTO supplier_property_mappings
                   (supplier_id,category_context,source_name,normalized_source_name,sample_value,
                    source_unit,inferred_type,canonical_property_id,status,confidence,evidence)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT(supplier_id,category_context,normalized_source_name) DO UPDATE SET
                    source_name=EXCLUDED.source_name,sample_value=EXCLUDED.sample_value,
                    source_unit=EXCLUDED.source_unit,inferred_type=EXCLUDED.inferred_type,
                    evidence=supplier_property_mappings.evidence || EXCLUDED.evidence,
                    canonical_property_id=CASE WHEN supplier_property_mappings.status IN ('UNMAPPED','AUTO_PROPOSED')
                      THEN EXCLUDED.canonical_property_id ELSE supplier_property_mappings.canonical_property_id END,
                    status=CASE WHEN supplier_property_mappings.status IN ('UNMAPPED','AUTO_PROPOSED')
                      THEN EXCLUDED.status ELSE supplier_property_mappings.status END,
                    confidence=CASE WHEN supplier_property_mappings.status IN ('UNMAPPED','AUTO_PROPOSED')
                      THEN EXCLUDED.confidence ELSE supplier_property_mappings.confidence END,
                    updated_at=now()""",
                (supplier_id, category_context or "", raw_name, normalized_name or normalize_text(raw_name),
                 sample, raw_unit or None, inferred_type, candidate[0] if candidate else None,
                 "AUTO_PROPOSED" if candidate else "UNMAPPED", .95 if candidate else None,
                json.dumps(evidence, ensure_ascii=False)),
            )
        cursor.execute("""UPDATE supplier_property_mappings m SET
              canonical_property_id=CASE WHEN r.config->>'canonical_property_id' ~ '^[0-9]+$' THEN (r.config->>'canonical_property_id')::bigint END,
              status='MANUAL_CONFIRMED',confirmed_by=r.created_by,
              evidence=m.evidence || jsonb_build_object('business_rule_id',r.id),updated_at=now()
            FROM business_rules r JOIN canonical_properties c
              ON c.id=CASE WHEN r.config->>'canonical_property_id' ~ '^[0-9]+$' THEN (r.config->>'canonical_property_id')::bigint END AND c.active
            WHERE r.active AND r.rule_type='PROPERTY_MAPPING_OVERRIDE' AND r.scope='property'
              AND r.supplier_id=m.supplier_id AND r.entity_key=m.normalized_source_name
              AND m.supplier_id=%s""",(supplier_id,))
        return len(rows)

    @staticmethod
    def _reconcile_sterbrust_property_evidence(cursor) -> int:
        cursor.execute("""SELECT DISTINCT item->>'property_id',item->>'property_code',
              coalesce(item->>'name',item->>'property_name',item->>'property_code')
            FROM sterbrust_products sp
            CROSS JOIN LATERAL jsonb_array_elements(coalesce(sp.properties->'items','[]'::jsonb)) item
            WHERE coalesce(item->>'property_id',item->>'property_code') IS NOT NULL""")
        source=list(cursor.fetchall()); created=0
        for property_id,code,name in source:
            canonical_key=canonical_property_name(name or code or "")
            if not canonical_key: continue
            cursor.execute("SELECT id FROM canonical_properties WHERE code=%s AND active",(canonical_key.upper(),))
            canonical=cursor.fetchone()
            if not canonical: continue
            cursor.execute("""INSERT INTO sterbrust_property_mappings
              (canonical_property_id,category_context,sterbrust_property_id,sterbrust_code,sterbrust_name,
               filter_enabled_evidence,status,comment)
              VALUES(%s,'',%s,%s,%s,%s::jsonb,'REVIEW','Exact ID/code observed; semantic mapping requires review')
              ON CONFLICT(canonical_property_id,category_context) DO UPDATE SET
               sterbrust_property_id=EXCLUDED.sterbrust_property_id,sterbrust_code=EXCLUDED.sterbrust_code,
               sterbrust_name=EXCLUDED.sterbrust_name,filter_enabled_evidence=EXCLUDED.filter_enabled_evidence,
               status=CASE WHEN sterbrust_property_mappings.status='MATCHED' THEN 'MATCHED' ELSE 'REVIEW' END,
               updated_at=now()""",
              (canonical[0],property_id or None,code or None,name or None,
               json.dumps({"source":"sterbrust_products.properties","id_observed":bool(property_id),"code_observed":bool(code)})))
            created+=1
        return created

    def reconcile_supplier_properties(self, supplier_code: str) -> int:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            supplier_id = self.ensure_supplier(supplier_code)
            self._bootstrap_canonical_properties(cursor)
            return self._reconcile_supplier_properties(cursor, supplier_id)

    def reconcile_sterbrust_property_evidence(self) -> int:
        """Reconcile only observed Sterbrust IDs/codes; never infer a confirmed semantic match."""
        with self.connection.transaction(), self.connection.cursor() as cursor:
            self._bootstrap_canonical_properties(cursor)
            return self._reconcile_sterbrust_property_evidence(cursor)

    @staticmethod
    def _price_provenance(card: ProductCard) -> tuple[str, str | None, str | None]:
        raw = card.raw_data or {}
        source_record = raw.get("source_record") if isinstance(raw.get("source_record"), dict) else {}
        commercial = source_record.get("commercial") if isinstance(source_record.get("commercial"), dict) else {}
        price_type = str(card.price_type or raw.get("price_type") or commercial.get("price_type") or "unknown")
        if price_type not in {"RRP", "retail", "wholesale", "dealer", "promo", "unknown"}:
            price_type = "unknown"
        price_raw = card.price_raw or raw.get("price_raw") or commercial.get("price_raw") or commercial.get("price")
        if price_raw in (None, "") and card.price is not None:
            price_raw = format(card.price, "f")
        price_source = card.price_source or raw.get("price_source") or commercial.get("price_source")
        if not price_source and card.price is not None:
            price_source = "adapter ProductCard.price"
        return price_type, None if price_raw in (None, "") else str(price_raw), price_source or None

    @staticmethod
    def _record_field_observations(cursor, product_id: int, card: ProductCard, observed_at: str) -> None:
        source_kind = str((card.raw_data or {}).get("source_kind") or "supplier_html")
        if source_kind not in {"supplier_html", "supplier_json", "supplier_xml", "supplier_api"}:
            source_kind = "supplier_html"
        price_type, price_raw, price_source = PostgresRepository._price_provenance(card)
        observations = {
            "sku": (card.sku, "ProductCard.sku", "adapter identity normalization"),
            "article": (card.sku, "ProductCard.sku", "supplier-scoped article"),
            "brand": (card.brand, "ProductCard.brand", "adapter brand normalization"),
            "model": ((card.raw_data or {}).get("model"), "raw_data.model", "identity model extraction"),
            "price": (card.price, price_source or "ProductCard.price", f"price_type={price_type}; raw={price_raw or ''}"),
            "currency": (card.currency, "ProductCard.currency", "adapter currency normalization"),
            "availability": ({"raw": card.availability_raw, "normalized": card.availability_normalized}, "ProductCard.availability_*", "availability normalization"),
            "description": (card.description_text, "ProductCard.description_text", "adapter text extraction"),
            "properties": ([{"name": x.name, "value": x.value, "unit": x.unit} for x in card.properties], "ProductCard.properties", "adapter property normalization"),
            "images": ([x.url for x in card.media if x.media_type == "image"], "ProductCard.media[image]", "adapter media normalization"),
            "relations": ([{"name": x.name, "values": [v.target_url for v in x.values]} for x in card.options], "ProductCard.options", "adapter relation normalization"),
        }
        for field_name, (value, source_path, normalized_by) in observations.items():
            cursor.execute(
                """INSERT INTO field_observations
                   (source_product_id,field_name,value,source_kind,source_path,observed_at,normalized_by)
                   VALUES (%s,%s,%s::jsonb,%s,%s,%s,%s)
                   ON CONFLICT(source_product_id,field_name) DO UPDATE SET
                     value=EXCLUDED.value,source_kind=EXCLUDED.source_kind,
                     source_path=EXCLUDED.source_path,observed_at=EXCLUDED.observed_at,
                     normalized_by=EXCLUDED.normalized_by""",
                (product_id, field_name, json.dumps(value, ensure_ascii=False, default=str),
                 source_kind, source_path, observed_at, normalized_by),
            )

    @staticmethod
    def _record_seen_observation(cursor, product_id: int, crawl_run_id: int | None, observed_at: str) -> None:
        event = json.dumps({"crawl_run_id": crawl_run_id, "evidence_type": "SEEN", "observed_at": observed_at})
        cursor.execute(
            """INSERT INTO missing_observations
               (source_product_id,consecutive_confirmed,last_evidence_type,state,first_missing_at,
                last_observed_at,last_crawl_run_id,history)
               VALUES (%s,0,'SEEN','ACTIVE',NULL,%s,%s,jsonb_build_array(%s::jsonb))
               ON CONFLICT(source_product_id) DO UPDATE SET consecutive_confirmed=0,
                last_evidence_type='SEEN',state='ACTIVE',first_missing_at=NULL,
                last_observed_at=EXCLUDED.last_observed_at,last_crawl_run_id=EXCLUDED.last_crawl_run_id,
                history=(missing_observations.history || %s::jsonb)""",
            (product_id, observed_at, crawl_run_id, event, event),
        )

    @staticmethod
    def _quality_profile(cards: list[ProductCard], products_seen: int) -> QualityProfile:
        parsed = len(cards)
        denominator = max(1, parsed)
        rates = {
            "price": sum(card.price is not None for card in cards) / denominator,
            "sku": sum(bool(card.sku.strip()) for card in cards) / denominator,
            "brand": sum(bool(card.brand.strip()) for card in cards) / denominator,
            "description": sum(bool(card.description_text.strip()) for card in cards) / denominator,
            "property": sum(bool(card.properties) for card in cards) / denominator,
            "image": sum(any(x.media_type == "image" for x in card.media) for card in cards) / denominator,
            "category": sum(bool(card.categories) for card in cards) / denominator,
        }
        if not parsed:
            rates = {key: 0.0 for key in rates}
        return QualityProfile(products_seen=products_seen, products_parsed=parsed, fill_rates=rates)

    @staticmethod
    def _record_quality_snapshot(cursor, supplier_id: int, crawl_run_id: int,
                                 cards: list[ProductCard], products_seen: int,
                                 *, schema_signals: list[str],
                                 crawl_settings: dict[str, Any] | None = None,
                                 base_url: str | None = None):
        cursor.execute(
            """SELECT d.products_seen,d.products_parsed,d.metrics
               FROM data_quality_snapshots d JOIN crawl_runs r ON r.id=d.crawl_run_id
               WHERE d.supplier_id=%s AND d.crawl_run_id<>%s AND NOT d.critical
                 AND r.status='succeeded' ORDER BY d.created_at DESC,d.id DESC LIMIT 1""",
            (supplier_id, crawl_run_id),
        )
        previous_row = cursor.fetchone()
        previous = None
        if previous_row:
            previous = QualityProfile(previous_row[0], previous_row[1], previous_row[2].get("fill_rates", previous_row[2]))
        current = PostgresRepository._quality_profile(cards, products_seen)
        automatic_signals = set(schema_signals)
        if previous:
            for field_name in ("price", "sku", "description", "property", "image"):
                if previous.fill_rates.get(field_name, 0) >= .5 and current.fill_rates.get(field_name, 0) == 0:
                    automatic_signals.add(f"{field_name}_block_missing")
        if crawl_settings is None or base_url is None:
            cursor.execute("SELECT crawl_settings,base_url FROM suppliers WHERE id=%s", (supplier_id,))
            live_settings, live_url = cursor.fetchone()
            if crawl_settings is None:
                crawl_settings = live_settings
            if base_url is None:
                base_url = live_url
        policy = (crawl_settings or {}).get("data_quality_policy", {}) or {}
        thresholds = AnomalyThresholds(
            count_ratio_min=float(policy.get("count_ratio_min", .60)),
            fill_rate_drop=float(policy.get("fill_rate_drop", .35)),
            fill_rate_min=float(policy.get("fill_rate_min", .25)),
        )
        anomaly = detect_anomalies(previous, current, thresholds, schema_signals=sorted(automatic_signals))
        metrics = {"fill_rates": dict(current.fill_rates), **{f"{key}_fill_rate": value for key, value in current.fill_rates.items()}}
        cursor.execute(
            """INSERT INTO data_quality_snapshots
               (supplier_id,crawl_run_id,products_seen,products_parsed,metrics,anomaly_codes,critical)
               VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)""",
            (supplier_id, crawl_run_id, current.products_seen, current.products_parsed,
             json.dumps(metrics), list(anomaly.codes), anomaly.critical),
        )
        if anomaly.critical:
            cursor.execute(
                """INSERT INTO crawl_errors(crawl_run_id,supplier_id,url,error_type,message,details)
                   VALUES (%s,%s,%s,'DATA_QUALITY_ANOMALY',%s,%s::jsonb)""",
                (crawl_run_id, supplier_id, base_url, ", ".join(anomaly.codes),
                 json.dumps(anomaly.evidence, ensure_ascii=False)),
            )
            if "PARSER_SCHEMA_DRIFT" in anomaly.codes:
                cursor.execute(
                    """INSERT INTO diagnostic_evidence_snapshots
                       (supplier_id,crawl_run_id,evidence_type,source_url,http_metadata,sanitized_excerpt,parser_version)
                       VALUES (%s,%s,'schema_drift',%s,%s::jsonb,%s,'stage6e.1-runtime')""",
                    (supplier_id, crawl_run_id, base_url,
                     json.dumps({"signals": sorted(automatic_signals)}),
                     ", ".join(sorted(automatic_signals))[:2000]),
                )
        return anomaly

    @staticmethod
    def _record_history(cursor, crawl_run_id: int, supplier_id: int,
                        source_product_id: int | None, offer_id: int | None,
                        field_group: str, old_value: Any, new_value: Any,
                        changed_at: str) -> None:
        if old_value == new_value:
            return
        cursor.execute(
            """INSERT INTO entity_change_history
               (crawl_run_id, supplier_id, source_product_id, offer_id, field_group,
                old_value, new_value, changed_at)
               VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)""",
            (crawl_run_id, supplier_id, source_product_id, offer_id, field_group,
             json.dumps(old_value, ensure_ascii=False, default=str),
             json.dumps(new_value, ensure_ascii=False, default=str), changed_at),
        )

    @staticmethod
    def _upsert_product_urls(cursor, product_id: int, card: ProductCard, observed_at: str) -> None:
        requested = card.requested_url.strip()
        final = card.final_url.strip()
        canonical = card.canonical_url.strip()
        # One row per URL is mandated by the schema. Use the strongest semantic
        # kind when the same URL plays multiple roles.
        urls: dict[str, str] = {}
        if requested:
            urls[requested] = "source"
        for url in card.redirect_urls:
            if url and url not in urls:
                urls[url] = "redirect"
        for url in card.alias_urls:
            if url and url not in urls:
                urls[url] = "alias"
        if final and final not in urls:
            urls[final] = "alias"
        if canonical and canonical != requested:
            urls[canonical] = "canonical"
        current_url = canonical or final or requested
        cursor.execute("UPDATE source_product_urls SET is_current=false WHERE source_product_id=%s AND is_current", (product_id,))
        for url, kind in urls.items():
            cursor.execute(
                """INSERT INTO source_product_urls
                   (source_product_id, url, url_kind, is_current, first_seen_at, last_seen_at)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source_product_id, url) DO UPDATE SET
                    url_kind=EXCLUDED.url_kind, is_current=EXCLUDED.is_current,
                    last_seen_at=EXCLUDED.last_seen_at""",
                (product_id, url, kind, url == current_url, observed_at, observed_at),
            )

    def final_crawl_run_status(self, run_id: int) -> str:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT status FROM crawl_runs WHERE id=%s", (run_id,))
            row = cursor.fetchone()
            if not row:
                raise RuntimeError(f"crawl run {run_id} was not persisted")
            return row[0]

    @staticmethod
    def _apply_missing(cursor, supplier_id: int, crawl_run_id: int, observed_ids: set[int],
                       observed_at: str, deactivation_allowed: bool, counts: IngestCounts,
                       *, discovered_urls: set[str] | None = None,
                       anomaly_guarded: bool = False,
                       missing_policy: MissingPolicy | None = None) -> None:
        if not deactivation_allowed:
            return
        policy = missing_policy or MissingPolicy()
        discovered_urls = {url.rstrip("/") for url in (discovered_urls or set()) if url}
        cursor.execute(
            """SELECT sp.id, sp.source_url, sp.missed_crawls, sp.active,
                       s.deactivate_after_misses,
                       coalesce(mo.consecutive_confirmed,sp.missed_crawls),
                       coalesce(mo.state,'ACTIVE'),
                       err.error_type
               FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
               LEFT JOIN missing_observations mo ON mo.source_product_id=sp.id
               LEFT JOIN LATERAL (
                 SELECT cri.error_type FROM crawl_run_items cri
                 WHERE cri.crawl_run_id=%s AND cri.item_status='error'
                   AND rtrim(cri.discovered_url,'/') IN (rtrim(sp.source_url,'/'),rtrim(coalesce(sp.canonical_url,sp.source_url),'/'))
                 ORDER BY cri.processed_at DESC LIMIT 1
               ) err ON true
               WHERE sp.supplier_id=%s AND NOT (sp.id = ANY(%s)) FOR UPDATE OF sp""",
            (crawl_run_id, supplier_id, list(observed_ids)),
        )
        missing = cursor.fetchall()
        for row in missing:
            product_id, source_url, old_misses, was_active, threshold = row[:5]
            confirmed = row[5] if len(row) > 5 else old_misses
            old_state = row[6] if len(row) > 6 else (policy.state_for(old_misses) if old_misses else "ACTIVE")
            error_type = row[7] if len(row) > 7 else None
            normalized_url = str(source_url).rstrip("/")
            if error_type in {"TIMEOUT", "HTTP_403", "HTTP_429", "NETWORK_ERROR", "PARSER_ERROR"}:
                evidence_type = error_type
            elif error_type in {"HTTP_404", "HTTP_410"}:
                evidence_type = error_type
            elif normalized_url in discovered_urls:
                # Discovery saw the URL but no card/error was produced. This is
                # a parser failure, never evidence that the product disappeared.
                evidence_type = "PARSER_ERROR"
            else:
                evidence_type = "NOT_DISCOVERED"
            confirmed_missing = evidence_type in {"HTTP_404", "HTTP_410", "NOT_IN_SITEMAP", "NOT_DISCOVERED"}
            if anomaly_guarded or not confirmed_missing:
                event = json.dumps({"crawl_run_id": crawl_run_id, "evidence_type": evidence_type,
                                    "observed_at": observed_at, "counter_changed": False})
                cursor.execute(
                    """INSERT INTO missing_observations
                       (source_product_id,consecutive_confirmed,last_evidence_type,state,first_missing_at,
                        last_observed_at,last_crawl_run_id,history)
                       VALUES (%s,%s,%s,%s,NULL,%s,%s,jsonb_build_array(%s::jsonb))
                       ON CONFLICT(source_product_id) DO UPDATE SET
                        last_evidence_type=EXCLUDED.last_evidence_type,
                        last_observed_at=EXCLUDED.last_observed_at,
                        last_crawl_run_id=EXCLUDED.last_crawl_run_id,
                        history=missing_observations.history || %s::jsonb""",
                    (product_id, confirmed, evidence_type, old_state, observed_at, crawl_run_id, event, event),
                )
                continue
            new_misses = confirmed + 1
            active = bool(was_active and new_misses < threshold)
            state = policy.state_for(new_misses)
            cursor.execute(
                "UPDATE source_products SET missed_crawls=%s, active=%s, updated_at=now() WHERE id=%s",
                (new_misses, active, product_id),
            )
            cursor.execute(
                """UPDATE offers SET missed_crawls=missed_crawls+1,
                   active=CASE WHEN missed_crawls+1 >= %s THEN false ELSE active END,
                   updated_at=now() WHERE source_product_id=%s AND offer_kind='default'""",
                (threshold, product_id),
            )
            cursor.execute(
                """INSERT INTO crawl_run_items
                   (crawl_run_id, supplier_id, source_product_id, discovered_url, item_status,
                    raw_metadata)
                   VALUES (%s,%s,%s,%s,'missing',%s::jsonb)
                   ON CONFLICT (crawl_run_id, discovered_url) DO UPDATE SET
                    source_product_id=EXCLUDED.source_product_id, item_status='missing',
                    processed_at=now(), raw_metadata=EXCLUDED.raw_metadata""",
                (crawl_run_id, supplier_id, product_id, source_url,
                 json.dumps({"missed_crawls": new_misses, "active": active,
                             "evidence_type": evidence_type, "state": state}, ensure_ascii=False)),
            )
            event = json.dumps({"crawl_run_id": crawl_run_id, "evidence_type": evidence_type,
                                "observed_at": observed_at, "counter_changed": True,
                                "consecutive_confirmed": new_misses})
            cursor.execute(
                """INSERT INTO missing_observations
                   (source_product_id,consecutive_confirmed,last_evidence_type,state,first_missing_at,
                    last_observed_at,last_crawl_run_id,history)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,jsonb_build_array(%s::jsonb))
                   ON CONFLICT(source_product_id) DO UPDATE SET
                    consecutive_confirmed=EXCLUDED.consecutive_confirmed,
                    last_evidence_type=EXCLUDED.last_evidence_type,state=EXCLUDED.state,
                    first_missing_at=coalesce(missing_observations.first_missing_at,EXCLUDED.first_missing_at),
                    last_observed_at=EXCLUDED.last_observed_at,
                    last_crawl_run_id=EXCLUDED.last_crawl_run_id,
                    history=missing_observations.history || %s::jsonb""",
                (product_id, new_misses, evidence_type, state, observed_at, observed_at,
                 crawl_run_id, event, event),
            )
            counts.missing_products += 1
            counts.missing_offers += 1
            if was_active and not active:
                counts.deactivated_products += 1
                counts.deactivated_offers += 1

    def load_existing_links(self, supplier_code: str = PARTNER_CODE) -> dict[str, str]:
        """Read authoritative current mappings from source_products, never CSV."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT sp.external_id, sb.sterbrust_product_id
                   FROM source_products sp
                   JOIN suppliers s ON s.id=sp.supplier_id
                   JOIN sterbrust_products sb ON sb.catalog_product_id=sp.catalog_product_id
                   WHERE s.code=%s AND sp.external_id_is_stable
                     AND sp.catalog_product_id IS NOT NULL""",
                (supplier_code,),
            )
            return {str(external_id): str(sterbrust_id) for external_id, sterbrust_id in cursor.fetchall()}

    def load_existing_link_contexts(self, supplier_code: str = PARTNER_CODE) -> dict[str, dict[str, Any]]:
        """Load mappings with lineage-derived automatic/manual provenance."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""WITH RECURSIVE lineage AS (
                       SELECT pm.id,pm.previous_match_id,pm.source_product_id,pm.match_method,pm.auto_accepted,0 depth
                       FROM product_matches pm WHERE pm.is_current
                       UNION ALL
                       SELECT prior.id,prior.previous_match_id,prior.source_product_id,
                              prior.match_method,prior.auto_accepted,lineage.depth+1
                       FROM lineage JOIN product_matches prior ON prior.id=lineage.previous_match_id
                     ), origin AS (
                       SELECT DISTINCT ON (source_product_id) source_product_id,match_method,auto_accepted
                       FROM lineage WHERE auto_accepted ORDER BY source_product_id,depth DESC
                     )
                     SELECT sp.external_id,sb.sterbrust_product_id,
                            coalesce(current_match.auto_accepted,false),coalesce(origin.match_method,''),
                            (
                              (NOT coalesce(current_match.auto_accepted,false)
                               AND {accepted_current_mapping_sql('sp.id','sp.catalog_product_id','current_match')})
                              OR {effective_manual_mapping_sql('sp.id','sp.catalog_product_id')}
                            )
                     FROM source_products sp
                     JOIN suppliers s ON s.id=sp.supplier_id
                     JOIN sterbrust_products sb ON sb.catalog_product_id=sp.catalog_product_id
                     LEFT JOIN product_matches current_match
                       ON current_match.source_product_id=sp.id AND current_match.is_current
                     LEFT JOIN origin ON origin.source_product_id=sp.id
                     WHERE s.code=%s AND sp.external_id_is_stable
                       AND sp.catalog_product_id IS NOT NULL""",
                (supplier_code,),
            )
            return {
                str(external_id): {
                    "sterbrust_product_id": str(sterbrust_id),
                    "current_auto_accepted": bool(auto_accepted),
                    "origin_match_method": str(origin_method),
                    "manual_or_human_confirmed": bool(manual),
                }
                for external_id, sterbrust_id, auto_accepted, origin_method, manual in cursor.fetchall()
            }

    def table_counts(self) -> dict[str, int]:
        tables = (
            "suppliers", "crawl_runs", "crawl_run_items", "crawl_errors", "source_products",
            "offers", "source_product_urls", "supplier_categories", "source_product_categories",
            "product_category_paths", "product_category_path_nodes", "product_properties",
            "product_media", "source_product_options", "source_product_option_values",
            "source_product_relations", "entity_change_history", "sterbrust_products",
            "sterbrust_product_supplier_identifiers", "catalog_products", "product_matches",
        )
        result: dict[str, int] = {}
        with self.connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f"SELECT count(*) FROM {table}")
                result[table] = cursor.fetchone()[0]
        return result

    def persist_match(self, decision: dict, validated_at: str,
                      supplier_code: str = PARTNER_CODE) -> str:
        """Persist one current decision without duplicating unchanged history."""
        evidence = json.loads(decision.get("evidence") or "{}") if isinstance(decision.get("evidence"), str) else decision.get("evidence", {})
        warnings = json.loads(decision.get("warnings") or "{}") if isinstance(decision.get("warnings"), str) else decision.get("warnings", {})
        conflicts = json.loads(decision.get("conflicts") or "{}") if isinstance(decision.get("conflicts"), str) else decision.get("conflicts", {})
        conflict_class = str(decision.get("conflict_class") or evidence.get("conflict_class") or "")
        candidate_key = str(decision.get("sterbrust_product_id") or "") or None
        decision_hash = decision_fingerprint(decision["status"], decision["match_method"], candidate_key, conflict_class)
        warning_hash = warning_fingerprint(warnings)
        with self.connection.transaction(), self.connection.cursor() as cursor:
            # AdminStore.decide locks review case before source. Use the same
            # order, so decision supersession and quarantine are serialized.
            cursor.execute(
                """SELECT rc.id FROM review_cases rc
                   JOIN source_products sp ON sp.id=rc.source_product_id
                   JOIN suppliers s ON s.id=sp.supplier_id
                   WHERE s.code=%s AND sp.external_id=%s AND sp.external_id_is_stable
                   FOR UPDATE OF rc""", (supplier_code, decision["source_external_id"]),
            )
            cursor.fetchall()
            cursor.execute(
                """SELECT sp.id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                   WHERE s.code=%s AND sp.external_id=%s AND sp.external_id_is_stable FOR UPDATE""",
                (supplier_code, decision["source_external_id"]),
            )
            source_row = cursor.fetchone()
            if source_row is None:
                raise RuntimeError(f"source product not ingested: {decision['source_external_id']}")
            source_id = source_row[0]
            catalog_id = None
            if candidate_key:
                cursor.execute("SELECT catalog_product_id FROM sterbrust_products WHERE sterbrust_product_id=%s", (candidate_key,))
                candidate_row = cursor.fetchone()
                catalog_id = candidate_row[0] if candidate_row else None
            cursor.execute(
                """SELECT id, decision_fingerprint, warning_fingerprint, auto_accepted, catalog_product_id
                   FROM product_matches
                   WHERE source_product_id=%s AND is_current FOR UPDATE""", (source_id,),
            )
            current = cursor.fetchone()
            if current and current[1] == decision_hash and current[2] == warning_hash:
                cursor.execute(
                    "UPDATE product_matches SET last_validated_at=GREATEST(%s::timestamptz, now()) WHERE id=%s",
                    (validated_at, current[0]),
                )
                if decision.get("auto_accepted") in {True, "TRUE"} and catalog_id is not None:
                    cursor.execute("UPDATE source_products SET catalog_product_id=%s, updated_at=now() WHERE id=%s", (catalog_id, source_id))
                return "VALIDATED_UNCHANGED"
            if current:
                cursor.execute("UPDATE product_matches SET is_current=false WHERE id=%s", (current[0],))
            auto_accepted = decision.get("auto_accepted") in {True, "TRUE"} and catalog_id is not None
            data_quality_flags = decision_data_quality_flags(decision, evidence)
            if not isinstance(data_quality_flags, list):
                data_quality_flags = []
            cursor.execute(
                """INSERT INTO product_matches
                   (source_product_id, catalog_product_id, status, match_method, confidence_score, evidence,
                    conflicts, warnings, data_quality_flags, rule_version, auto_accepted, accepted_at,
                    last_validated_at, is_current, previous_match_id, decision_fingerprint, warning_fingerprint)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,'stage3b-1.0',%s,
                           CASE WHEN %s THEN %s::timestamptz ELSE NULL END,
                           GREATEST(%s::timestamptz, now()),true,%s,%s,%s)""",
                (source_id, catalog_id, decision["status"], decision["match_method"], decision["confidence_score"],
                 json.dumps(evidence, ensure_ascii=False), json.dumps(conflicts, ensure_ascii=False),
                 json.dumps(warnings, ensure_ascii=False), json.dumps(data_quality_flags, ensure_ascii=False),
                 auto_accepted, auto_accepted, validated_at, validated_at, current[0] if current else None,
                 decision_hash, warning_hash),
            )
            if auto_accepted:
                cursor.execute("UPDATE source_products SET catalog_product_id=%s, updated_at=now() WHERE id=%s", (catalog_id, source_id))
            elif current and should_quarantine_automatic_link(current[3], decision["status"]):
                # Review sync uses the latest final manual decision, ignoring
                # POSTPONED (queue scheduling, not mapping revocation). A newer
                # final decision supersedes an older confirmation. Protect only
                # the exact current source/target, not any historical manual row.
                cursor.execute(
                    f"""UPDATE source_products sp SET catalog_product_id=NULL, updated_at=now()
                       WHERE sp.id=%s AND sp.catalog_product_id=%s
                         AND NOT {effective_manual_mapping_sql('sp.id','sp.catalog_product_id')}""",
                    (source_id, current[4]),
                )
            return "INSERTED_EVENT"

    def load_sterbrust_snapshot(self, registry_path: Path, property_map_path: Path) -> int:
        """Idempotently load the approved read-only Sterbrust snapshot."""
        import csv
        from collections import defaultdict
        with property_map_path.open(encoding="utf-8-sig", newline="") as handle:
            supplier_by_property = {row["property_code"]: row["supplier_code"] for row in csv.DictReader(handle)}
        loaded = 0
        with self.connection.transaction(), self.connection.cursor() as cursor, registry_path.open(encoding="utf-8") as handle:
            cursor.execute("SELECT code, id FROM suppliers")
            supplier_ids = dict(cursor.fetchall())
            for line in handle:
                row = json.loads(line)
                captured = row["captured_at"]
                properties = row.get("properties", [])
                cursor.execute(
                    """INSERT INTO sterbrust_products
                       (sterbrust_product_id, name, normalized_name, article_raw, supplier_article_raw,
                        normalized_supplier_article, brand_raw, normalized_brand, manufacturer_raw,
                        model_raw, normalized_model, category_id, category_name, category_path,
                        category_metadata, properties, product_url, active, raw_data, snapshot_hash,
                        first_seen_at, last_seen_at, last_synced_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s)
                       ON CONFLICT (sterbrust_product_id) DO UPDATE SET name=EXCLUDED.name,
                        normalized_name=EXCLUDED.normalized_name, article_raw=EXCLUDED.article_raw,
                        supplier_article_raw=EXCLUDED.supplier_article_raw,
                        normalized_supplier_article=EXCLUDED.normalized_supplier_article,
                        brand_raw=EXCLUDED.brand_raw, normalized_brand=EXCLUDED.normalized_brand,
                        manufacturer_raw=EXCLUDED.manufacturer_raw, model_raw=EXCLUDED.model_raw,
                        normalized_model=EXCLUDED.normalized_model, category_id=EXCLUDED.category_id,
                        category_name=EXCLUDED.category_name, category_path=EXCLUDED.category_path,
                        category_metadata=EXCLUDED.category_metadata, properties=EXCLUDED.properties,
                        product_url=EXCLUDED.product_url, active=EXCLUDED.active, raw_data=EXCLUDED.raw_data,
                        snapshot_hash=EXCLUDED.snapshot_hash, last_seen_at=EXCLUDED.last_seen_at,
                        last_synced_at=EXCLUDED.last_synced_at, updated_at=now()""",
                    (str(row["sterbrust_product_id"]), row["name"], normalized_name(row["name"]),
                     row.get("article_raw") or None, row.get("supplier_article_raw") or None,
                     row.get("normalized_supplier_article") or None, row.get("brand_raw") or None,
                     normalize_brand(row.get("brand_raw")) or None, row.get("manufacturer_raw") or None,
                     row.get("model_raw") or None, normalize_model(row.get("model_raw"), row.get("brand_raw")) or None,
                     str(row.get("category_id") or "") or None, row.get("category_name") or None,
                     row.get("category_path") or None, json.dumps({"category_ids": row.get("category_ids", [])}),
                     json.dumps({"items": properties}, ensure_ascii=False),
                     str(row.get("product_url") or "").strip() or None,
                     str(row.get("active", "")).upper() in {"Y", "TRUE", "1"},
                     json.dumps(row.get("raw_data") or {}, ensure_ascii=False), row["snapshot_hash"],
                     captured, captured, captured),
                )
                cursor.execute("DELETE FROM sterbrust_product_supplier_identifiers WHERE sterbrust_product_id=%s", (str(row["sterbrust_product_id"]),))
                for item in properties:
                    supplier_code = supplier_by_property.get(str(item.get("property_code", "")))
                    if not supplier_code:
                        continue
                    values = item.get("value") if isinstance(item.get("value"), list) else [item.get("value")]
                    for value in values:
                        if isinstance(value, dict):
                            value = value.get("valueEnum") or value.get("value")
                            if isinstance(value, dict):
                                value = value.get("valueEnum") or value.get("value")
                        raw_value = str(value or "").strip()
                        normalized = normalize_model(raw_value)
                        if not normalized:
                            continue
                        cursor.execute(
                            """INSERT INTO sterbrust_product_supplier_identifiers
                               (sterbrust_product_id, supplier_id, supplier_code, property_id, property_code,
                                raw_value, normalized_value, first_seen_at, last_seen_at)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (str(row["sterbrust_product_id"]), supplier_ids.get(supplier_code), supplier_code,
                             int(item["property_id"]), item["property_code"], raw_value, normalized, captured, captured),
                        )
                loaded += 1
        return loaded

    def sync_sterbrust_identifier_catalog_links(self) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE sterbrust_product_supplier_identifiers i
                   SET supplier_id=s.id, updated_at=now()
                   FROM suppliers s
                   WHERE i.supplier_code=s.code AND i.supplier_id IS DISTINCT FROM s.id"""
            )
            cursor.execute(
                """UPDATE sterbrust_product_supplier_identifiers i SET catalog_product_id=p.catalog_product_id,
                     updated_at=now() FROM sterbrust_products p
                   WHERE p.sterbrust_product_id=i.sterbrust_product_id
                     AND i.catalog_product_id IS DISTINCT FROM p.catalog_product_id"""
            )

    def _replace_details(self, cursor, supplier_id: int, product_id: int, offer_id: int, card: ProductCard, observed_at: str) -> None:
        cursor.execute("DELETE FROM product_properties WHERE source_product_id=%s AND offer_id IS NULL", (product_id,))
        for item in card.properties:
            cursor.execute(
                """INSERT INTO product_properties
                   (source_product_id, raw_name, raw_value, raw_unit, position, normalized_name, normalized_value)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (product_id, item.name, item.value, item.unit or None, item.position,
                 normalize_text(item.name), normalize_text(item.value)),
            )
        seen_media = {kind: [item.url for item in card.media if item.media_type == kind]
                      for kind in ("image", "document", "video")}
        for item in card.media:
            cursor.execute(
                """INSERT INTO product_media
                   (source_product_id, media_type, source_url, title, document_type, position, is_primary,
                    raw_metadata, first_seen_at, last_seen_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb,%s,%s)
                   ON CONFLICT (source_product_id, media_type, source_url) WHERE offer_id IS NULL
                   DO UPDATE SET title=EXCLUDED.title, position=EXCLUDED.position, is_primary=EXCLUDED.is_primary,
                     last_seen_at=EXCLUDED.last_seen_at, updated_at=now()""",
                (product_id, item.media_type, item.url, item.title or None,
                 Path(item.url.split("?", 1)[0]).suffix.lower().lstrip(".") or None if item.media_type == "document" else None,
                 item.position, item.is_primary, observed_at, observed_at),
            )
        for media_type, urls in seen_media.items():
            if urls:
                cursor.execute(
                    "DELETE FROM product_media WHERE source_product_id=%s AND offer_id IS NULL AND media_type=%s AND NOT (source_url = ANY(%s))",
                    (product_id, media_type, urls),
                )
            else:
                cursor.execute(
                    "DELETE FROM product_media WHERE source_product_id=%s AND offer_id IS NULL AND media_type=%s",
                    (product_id, media_type),
                )
        cursor.execute("DELETE FROM source_product_options WHERE source_product_id=%s", (product_id,))
        for option in card.options:
            cursor.execute(
                "INSERT INTO source_product_options (supplier_id, source_product_id, raw_name, position) VALUES (%s,%s,%s,%s) RETURNING id",
                (supplier_id, product_id, option.name, option.position),
            )
            option_id = cursor.fetchone()[0]
            for value in option.values:
                cursor.execute(
                    """INSERT INTO source_product_option_values
                       (supplier_id, option_id, raw_value, position, target_url, variant_internal_id, classification, is_selected)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (supplier_id, option_id, value.value, value.position, value.target_url or None,
                     value.variant_internal_id or None, value.classification, value.selected),
                )
        cursor.execute("UPDATE product_category_paths SET is_current=false WHERE source_product_id=%s AND is_current", (product_id,))
        raw_path = [{"name": item.name, "url": item.url} for item in card.categories]
        cursor.execute(
            "INSERT INTO product_category_paths (supplier_id, source_product_id, path_type, is_current, raw_breadcrumb, observed_at) VALUES (%s,%s,'published',true,%s::jsonb,%s) RETURNING id",
            (supplier_id, product_id, json.dumps(raw_path, ensure_ascii=False), observed_at),
        )
        path_id = cursor.fetchone()[0]
        parent_id = None
        for item in card.categories:
            external_id = item.url.rstrip("/").rsplit("/", 1)[-1] or item.name
            cursor.execute(
                """INSERT INTO supplier_categories
                   (supplier_id, external_id, name, source_url, parent_id, depth, first_seen_at, last_seen_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (supplier_id, external_id) WHERE external_id IS NOT NULL DO UPDATE SET
                     name=EXCLUDED.name, source_url=EXCLUDED.source_url, parent_id=EXCLUDED.parent_id,
                     depth=EXCLUDED.depth,
                     first_seen_at=LEAST(supplier_categories.first_seen_at,EXCLUDED.first_seen_at),
                     last_seen_at=GREATEST(supplier_categories.last_seen_at,EXCLUDED.last_seen_at),
                     active=true, updated_at=now()
                   RETURNING id""",
                (supplier_id, external_id, item.name, item.url or None, parent_id, item.position, observed_at, observed_at),
            )
            category_id = cursor.fetchone()[0]
            cursor.execute(
                """INSERT INTO source_product_categories
                   (supplier_id, source_product_id, category_id, membership_source, first_seen_at, last_seen_at)
                   VALUES (%s,%s,%s,'breadcrumb',%s,%s)
                   ON CONFLICT (supplier_id, source_product_id, category_id) DO UPDATE SET
                     first_seen_at=LEAST(source_product_categories.first_seen_at,EXCLUDED.first_seen_at),
                     last_seen_at=GREATEST(source_product_categories.last_seen_at,EXCLUDED.last_seen_at)""",
                (supplier_id, product_id, category_id, observed_at, observed_at),
            )
            cursor.execute(
                "INSERT INTO product_category_path_nodes (supplier_id, path_id, position, category_id, raw_name, raw_url) VALUES (%s,%s,%s,%s,%s,%s)",
                (supplier_id, path_id, item.position, category_id, item.name, item.url or None),
            )
            parent_id = category_id

    def _upsert_navigation_relations(self, cursor, supplier_id: int, cards: list[ProductCard], url_to_product: dict[str, int], observed_at: str) -> None:
        cursor.execute("DELETE FROM source_product_relations WHERE supplier_id=%s AND relation_type='navigation'", (supplier_id,))
        for card in cards:
            source_id = url_to_product.get(card.canonical_url.rstrip("/"))
            for option in card.options:
                for value in option.values:
                    if value.classification != "NAVIGATION_TO_PRODUCT":
                        continue
                    target_id = url_to_product.get(value.target_url.rstrip("/"))
                    if source_id and target_id and source_id != target_id:
                        cursor.execute(
                            """INSERT INTO source_product_relations
                               (supplier_id, from_source_product_id, to_source_product_id, relation_type,
                                first_seen_at, last_seen_at)
                               VALUES (%s,%s,%s,'navigation',%s,%s)""",
                            (supplier_id, source_id, target_id, observed_at, observed_at),
                        )
