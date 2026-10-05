from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from universal_supplier.postgres import PostgresConfig
from .scheduler import next_occurrences


DECISIONS = (
    "EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING", "REVIEW_EXISTING", "CONFLICT",
    "SAFE_NEW_PRODUCT_CANDIDATE", "ACCESSORY_OR_COMPATIBILITY_ITEM", "INSUFFICIENT_IDENTITY",
)


class UnavailableControlPlaneStore:
    """Keeps the UI importable while reporting missing database configuration."""

    def __init__(self, message: str) -> None:
        self.message = message

    def health(self) -> dict:
        return {"application": "ok", "postgresql": "unavailable", "error": self.message}

    def dashboard(self): return []
    def filter_options(self): return {"suppliers": [], "brands": [], "categories": [], "availability": []}
    def products(self, filters): return {"rows": [], "total": 0, "page": filters.page, "page_size": filters.page_size, "pages": 0}
    def product(self, supplier_code, external_id): return None
    def suppliers(self): return []
    def runs(self): return []
    def run(self, run_id): return None
    def export_rows(self, filters): return []
    def product_history(self, supplier_code, external_id): return []

    def __getattr__(self, name):
        raise RuntimeError(f"database unavailable: {self.message}")


@dataclass(frozen=True)
class ProductFilters:
    supplier: str = ""
    brand: str = ""
    decision: str = ""
    product_kind: str = ""
    category: str = ""
    availability: str = ""
    content: str = ""
    media: str = ""
    search: str = ""
    quality_issue: str = ""
    page: int = 1
    page_size: int = 50

    def __post_init__(self) -> None:
        if self.page < 1 or self.page_size not in {25, 50, 100}:
            raise ValueError("invalid pagination")
        if self.decision and self.decision not in DECISIONS:
            raise ValueError("invalid decision")
        if self.quality_issue and self.quality_issue not in {"without_price","zero_price","without_currency","without_sku","without_brand","without_model","without_description","without_properties","without_images","without_category","without_availability"}:
            raise ValueError("invalid quality issue")


def product_where(filters: ProductFilters) -> tuple[str, list[Any]]:
    clauses = ["sp.active"]
    params: list[Any] = []
    mapping = (
        (filters.supplier, "s.code=%s"), (filters.brand, "sp.brand_raw=%s"),
        (filters.decision, "pid.decision=%s"), (filters.product_kind, "pid.product_kind=%s"),
        (filters.availability, "o.availability_normalized=%s"),
    )
    for value, clause in mapping:
        if value:
            clauses.append(clause)
            params.append(value)
    if filters.category:
        clauses.append("coalesce(cat.category_path,'') ILIKE %s")
        params.append(f"%{filters.category}%")
    if filters.content in {"complete", "missing"}:
        expression = "(coalesce(sp.description_text,'')<>'' AND coalesce(sp.brand_raw,'')<>'')"
        clauses.append(expression if filters.content == "complete" else f"NOT {expression}")
    if filters.media in {"complete", "missing"}:
        clauses.append("media.image_count>0" if filters.media == "complete" else "media.image_count=0")
    if filters.search:
        clauses.append("(sp.external_id ILIKE %s OR sp.sku ILIKE %s OR sp.name ILIKE %s OR pid.own_model ILIKE %s OR pid.reference_model ILIKE %s OR pid.best_sterbrust_id ILIKE %s)")
        params.extend([f"%{filters.search}%"] * 6)
    issue={
        "without_price":"o.price IS NULL","zero_price":"o.price=0","without_currency":"o.currency IS NULL",
        "without_sku":"nullif(btrim(sp.sku),'') IS NULL","without_brand":"nullif(btrim(sp.brand_raw),'') IS NULL",
        "without_model":"nullif(btrim(pid.own_model),'') IS NULL","without_description":"nullif(btrim(sp.description_text),'') IS NULL",
        "without_properties":"prop.property_count=0","without_images":"media.image_count=0",
        "without_category":"coalesce(cat.category_path,'')=''","without_availability":"o.availability_normalized IS NULL OR o.availability_normalized='unknown'",
    }.get(filters.quality_issue)
    if issue: clauses.append(f"({issue})")
    return " AND ".join(clauses), params


class PostgresControlPlaneStore:
    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    def _connect(self):
        import psycopg
        return psycopg.connect(**self.config.kwargs(), autocommit=True, row_factory=psycopg.rows.dict_row)

    def health(self) -> dict:
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok,current_setting('server_version') AS version")
                row = cursor.fetchone()
            return {"application": "ok", "postgresql": "ok", "version": row["version"]}
        except Exception as error:
            return {"application": "ok", "postgresql": "error", "error": str(error)[:200]}

    def dashboard(self) -> list[dict]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT s.id,s.code,s.name,s.enabled,s.base_url,s.rate_settings,s.crawl_settings,
                       count(DISTINCT sp.id) AS products,
                       count(DISTINCT ce.id) FILTER (WHERE ce.created_at>now()-interval '30 days') AS errors_30d,
                       last_run.id AS last_run_id,last_run.status AS last_status,last_run.started_at AS last_crawl,
                       last_run.metadata AS last_run_metadata,
                       last_run.discovered_count AS last_discovered_count,last_run.new_count AS last_new_count,
                       last_run.changed_count AS last_changed_count,last_run.error_count AS last_error_count,
                       CASE WHEN last_run.finished_at IS NULL THEN NULL ELSE round(extract(epoch FROM (last_run.finished_at-last_run.started_at)))::int END AS last_duration_seconds,
                       active.status AS job_status,active.id AS job_id,
                       ss.enabled AS schedule_enabled,ss.cron_expression,ss.timezone
                FROM suppliers s
                LEFT JOIN source_products sp ON sp.supplier_id=s.id AND sp.active
                LEFT JOIN crawl_errors ce ON ce.supplier_id=s.id
                LEFT JOIN LATERAL (SELECT id,status,started_at,finished_at,discovered_count,new_count,changed_count,error_count,metadata FROM crawl_runs WHERE supplier_id=s.id ORDER BY started_at DESC LIMIT 1) last_run ON true
                LEFT JOIN LATERAL (SELECT id,status FROM crawl_jobs WHERE supplier_id=s.id AND status IN ('queued','running') ORDER BY id LIMIT 1) active ON true
                LEFT JOIN supplier_schedules ss ON ss.supplier_id=s.id
                GROUP BY s.id,last_run.id,last_run.status,last_run.started_at,last_run.finished_at,last_run.discovered_count,last_run.new_count,last_run.changed_count,last_run.error_count,last_run.metadata,active.status,active.id,ss.enabled,ss.cron_expression,ss.timezone
                ORDER BY s.name,s.id""")
            rows = list(cursor.fetchall())
        for row in rows:
            row["next_runs"] = next_occurrences(row["cron_expression"], row["timezone"], count=3) if row.get("schedule_enabled") and row.get("cron_expression") else []
        return rows

    def filter_options(self) -> dict:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT code,name FROM suppliers ORDER BY name")
            suppliers = list(cursor.fetchall())
            cursor.execute("SELECT DISTINCT brand_raw AS value FROM source_products WHERE brand_raw IS NOT NULL AND btrim(brand_raw)<>'' ORDER BY value")
            brands = [row["value"] for row in cursor.fetchall()]
            cursor.execute("SELECT DISTINCT product_kind AS value FROM product_identity_decisions WHERE product_kind IS NOT NULL ORDER BY value")
            kinds = [row["value"] for row in cursor.fetchall()]
        return {"suppliers": suppliers, "brands": brands, "decisions": DECISIONS, "kinds": kinds}

    def products(self, filters: ProductFilters) -> dict:
        where, params = product_where(filters)
        joins = """FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
            LEFT JOIN product_identity_decisions pid ON pid.source_product_id=sp.id
            LEFT JOIN LATERAL (SELECT availability_normalized,price,quantity,currency FROM offers
                WHERE source_product_id=sp.id AND (active OR s.code IN ('intervesp','beka_mak','beka_mak_tr','kami'))
                ORDER BY active DESC,id LIMIT 1) o ON true
            LEFT JOIN LATERAL (SELECT price_state,price,currency,availability_normalized,observed_at,capture_id
                FROM offer_commercial_observations co WHERE co.source_product_id=sp.id
                  AND COALESCE(co.region_code,'')=COALESCE(sp.raw_data->>'_commercial_projection_region','')
                ORDER BY co.observed_at DESC,co.id DESC LIMIT 1) commercial ON true
            LEFT JOIN LATERAL (SELECT count(*) FILTER (WHERE media_type='image') AS image_count FROM product_media WHERE source_product_id=sp.id) media ON true
            LEFT JOIN LATERAL (SELECT count(*) AS property_count FROM product_properties WHERE source_product_id=sp.id) prop ON true
            LEFT JOIN LATERAL (SELECT string_agg(node.raw_name,' / ' ORDER BY node.position) AS category_path FROM product_category_paths path JOIN product_category_path_nodes node ON node.path_id=path.id WHERE path.source_product_id=sp.id AND path.is_current) cat ON true"""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) AS total {joins} WHERE {where}", params)
            total = cursor.fetchone()["total"]
            cursor.execute(f"""SELECT sp.id,sp.external_id,sp.sku,sp.name,sp.brand_raw,sp.source_url,s.code AS supplier_code,s.name AS supplier_name,
                       pid.decision,pid.product_kind,pid.own_model,pid.reference_model,pid.best_sterbrust_id,
                       pid.decision_reason,coalesce(cat.category_path,'') AS category,
                       coalesce(commercial.availability_normalized,o.availability_normalized) AS availability_normalized,
                       CASE WHEN commercial.price_state IN ('price_on_request','missing') THEN NULL
                            ELSE coalesce(commercial.price,o.price) END AS price,
                       coalesce(commercial.currency,o.currency) AS currency,
                       commercial.price_state,commercial.observed_at AS commercial_observed_at,
                       o.quantity,coalesce(media.image_count,0) AS image_count
                {joins} WHERE {where}
                ORDER BY s.code,coalesce(sp.brand_raw,''),sp.name,sp.id LIMIT %s OFFSET %s""",
                [*params, filters.page_size, (filters.page - 1) * filters.page_size])
            items = list(cursor.fetchall())
        return {"items": items, "total": total, "page": filters.page, "page_size": filters.page_size,
                "pages": max(1, (total + filters.page_size - 1) // filters.page_size)}

    def export_rows(self, filters: ProductFilters) -> list[dict]:
        """Compatibility materialization; web exports use iter_export_rows()."""
        return list(self.iter_export_rows(filters))

    def iter_export_rows(self, filters: ProductFilters, chunk_size: int = 500):
        """Yield the exact filtered set in bounded retrieval chunks."""
        where, params = product_where(filters)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"""SELECT sp.id AS source_product_id,s.code AS supplier_code,s.name AS supplier_name,
              sp.external_id,sp.external_id_is_stable,sp.sku,sp.name,sp.brand_raw,sp.manufacturer_raw,
              sp.source_url,sp.canonical_url,sp.description_text,sp.description_html,sp.active,
              sp.first_seen_at,sp.last_seen_at,sp.last_success_at,sp.last_changed_at,sp.last_http_status,
              pid.own_model,pid.reference_model,pid.product_kind,coalesce(pid.decision,accepted.match_status) AS decision,
              coalesce(pid.decision_reason,accepted.match_method) AS decision_reason,
              CASE WHEN pid.decision = 'REVIEW_EXISTING' THEN pid.best_sterbrust_id END AS proposed_sterbrust_id,
              coalesce(manual.sterbrust_product_id,accepted.sterbrust_product_id) AS confirmed_sterbrust_id,
              CASE WHEN manual.sterbrust_product_id IS NOT NULL THEN 'MANUAL_CONFIRMED'
                   WHEN accepted.sterbrust_product_id IS NOT NULL THEN 'AUTO_ACCEPTED' END AS confirmed_link_source,
              coalesce(pid.conflicts,accepted.conflicts) AS conflicts,pid.identity_characteristics,
              coalesce(pid.rule_version,accepted.rule_version) AS rule_version,coalesce(pid.audited_at,accepted.accepted_at) AS audited_at,
              coalesce(cat.category_path,'') AS category,o.price,o.old_price,o.currency,o.price_type,
              o.price_raw,o.price_source,o.availability_raw,o.availability_normalized,o.quantity,
              coalesce(prop.property_count,0) AS property_count,
              coalesce(media.image_count,0) AS image_count,coalesce(media.document_count,0) AS document_count,
              coalesce(rel.relation_count,0) AS relation_count,coalesce(opt.option_count,0) AS option_count,sp.sku AS article
              FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
              LEFT JOIN product_identity_decisions pid ON pid.source_product_id=sp.id
              LEFT JOIN LATERAL(SELECT sb.sterbrust_product_id,pm.status AS match_status,pm.match_method,
                pm.conflicts,pm.rule_version,coalesce(pm.accepted_at,pm.created_at) AS accepted_at
                FROM product_matches pm JOIN sterbrust_products sb ON sb.catalog_product_id=pm.catalog_product_id AND sb.active
                WHERE pm.source_product_id=sp.id AND pm.is_current AND pm.auto_accepted
                  AND pm.status IN ('EXACT_MATCH','HIGH_CONFIDENCE_MATCH')
                ORDER BY coalesce(pm.accepted_at,pm.created_at) DESC,pm.id DESC LIMIT 1) accepted ON true
              LEFT JOIN LATERAL(SELECT rd.sterbrust_product_id
                FROM review_cases rc JOIN review_decisions rd ON rd.review_case_id=rc.id
                JOIN sterbrust_products sb ON sb.sterbrust_product_id=rd.sterbrust_product_id AND sb.active
                WHERE rc.source_product_id=sp.id AND rc.lifecycle_status='RESOLVED'
                  AND rc.resolved_decision_class='MANUAL_CONFIRMED'
                  AND rd.decision_class='MANUAL_CONFIRMED'
                ORDER BY rd.created_at DESC,rd.id DESC LIMIT 1) manual ON true
              LEFT JOIN LATERAL(SELECT availability_raw,availability_normalized,price,old_price,currency,price_type,price_raw,price_source,quantity FROM offers WHERE source_product_id=sp.id AND active ORDER BY id LIMIT 1)o ON true
              LEFT JOIN LATERAL(SELECT count(*) AS property_count FROM product_properties WHERE source_product_id=sp.id)prop ON true
              LEFT JOIN LATERAL(SELECT count(*) FILTER(WHERE media_type='image') AS image_count,count(*) FILTER(WHERE media_type='document') AS document_count FROM product_media WHERE source_product_id=sp.id)media ON true
              LEFT JOIN LATERAL(SELECT count(*) AS relation_count FROM source_product_relations WHERE from_source_product_id=sp.id)rel ON true
              LEFT JOIN LATERAL(SELECT count(*) AS option_count FROM source_product_options WHERE source_product_id=sp.id)opt ON true
              LEFT JOIN LATERAL(SELECT string_agg(node.raw_name,' / ' ORDER BY node.position) AS category_path FROM product_category_paths path JOIN product_category_path_nodes node ON node.path_id=path.id AND node.supplier_id=path.supplier_id WHERE path.source_product_id=sp.id AND path.is_current)cat ON true
              WHERE {where} ORDER BY s.code,sp.id""", params)
            while True:
                rows=cursor.fetchmany(chunk_size)
                if not rows: break
                yield from rows

    def export_details(self, source_product_ids: list[int]) -> dict[str, list[dict]]:
        empty = {"properties": [], "images": [], "documents": [], "relations": [], "options": [],
                 "provenance": [], "matches": [], "decisions": []}
        if not source_product_ids:
            return empty
        with self._connect() as connection, connection.cursor() as cursor:
            base = """SELECT s.code AS supplier_code,sp.external_id,sp.id AS source_product_id"""
            cursor.execute(base+",p.raw_name,p.raw_value,p.raw_unit,p.normalized_name,p.normalized_value,p.normalized_unit,p.raw_metadata,sm.status AS mapping_status,cp.code AS canonical_code,cp.display_name_ru AS canonical_name,cp.canonical_unit FROM product_properties p JOIN source_products sp ON sp.id=p.source_product_id JOIN suppliers s ON s.id=sp.supplier_id LEFT JOIN supplier_property_mappings sm ON sm.supplier_id=sp.supplier_id AND sm.normalized_source_name=p.normalized_name LEFT JOIN canonical_properties cp ON cp.id=sm.canonical_property_id WHERE sp.id=ANY(%s) ORDER BY sp.id,p.position,p.id", (source_product_ids,)); empty["properties"]=list(cursor.fetchall())
            cursor.execute(base+",m.source_url,m.is_primary,m.document_type FROM product_media m JOIN source_products sp ON sp.id=m.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.id=ANY(%s) AND m.media_type='image' ORDER BY sp.id,m.position,m.id", (source_product_ids,)); empty["images"]=list(cursor.fetchall())
            cursor.execute(base+",m.source_url,m.document_type FROM product_media m JOIN source_products sp ON sp.id=m.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.id=ANY(%s) AND m.media_type='document' ORDER BY sp.id,m.position,m.id", (source_product_ids,)); empty["documents"]=list(cursor.fetchall())
            cursor.execute(base+",r.relation_type,r.to_source_product_id FROM source_product_relations r JOIN source_products sp ON sp.id=r.from_source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.id=ANY(%s) ORDER BY sp.id,r.id", (source_product_ids,)); empty["relations"]=list(cursor.fetchall())
            cursor.execute(base+",o.raw_name AS option_name,o.position AS option_position,v.raw_value,v.position,v.target_url,v.variant_internal_id,v.classification,v.is_selected FROM source_product_options o JOIN source_products sp ON sp.id=o.source_product_id JOIN suppliers s ON s.id=sp.supplier_id JOIN source_product_option_values v ON v.option_id=o.id WHERE sp.id=ANY(%s) ORDER BY sp.id,o.position,o.id,v.position,v.id", (source_product_ids,)); empty["options"]=list(cursor.fetchall())
            cursor.execute(base+",f.field_name,f.source_kind,f.source_path,f.observed_at,f.normalized_by FROM field_observations f JOIN source_products sp ON sp.id=f.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.id=ANY(%s) ORDER BY sp.id,f.field_name", (source_product_ids,)); empty["provenance"]=list(cursor.fetchall())
            cursor.execute(base+",pid.best_sterbrust_id AS sterbrust_product_id,pid.decision FROM product_identity_decisions pid JOIN source_products sp ON sp.id=pid.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.id=ANY(%s) ORDER BY sp.id", (source_product_ids,)); empty["matches"]=list(cursor.fetchall())
            cursor.execute(base+",rd.decision_class,rd.created_at,rd.user_id FROM review_decisions rd JOIN source_products sp ON sp.id=rd.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.id=ANY(%s) ORDER BY sp.id,rd.created_at,rd.id", (source_product_ids,)); empty["decisions"]=list(cursor.fetchall())
        return empty

    def iter_export_detail(self, filters: ProductFilters, kind: str, chunk_size: int = 500):
        if kind not in {"properties","images","documents","relations","options","provenance","matches","decisions"}:
            raise ValueError("unknown export detail kind")
        batch: list[int] = []
        for row in self.iter_export_rows(filters,chunk_size=chunk_size):
            batch.append(int(row["source_product_id"]))
            if len(batch) >= chunk_size:
                yield from self.export_details(batch)[kind]
                batch.clear()
        if batch:
            yield from self.export_details(batch)[kind]

    def change_rows(self, *, period: str = "7d", run_a: int | None = None, run_b: int | None = None) -> list[dict]:
        clauses=[]; params=[]
        if period=="last_run": clauses.append("h.crawl_run_id=(SELECT max(id) FROM crawl_runs)")
        elif period=="24h": clauses.append("h.changed_at>=now()-interval '24 hours'")
        elif period=="7d": clauses.append("h.changed_at>=now()-interval '7 days'")
        elif period=="between" and run_a is not None and run_b is not None:
            clauses.append("h.crawl_run_id BETWEEN %s AND %s"); params.extend(sorted((run_a,run_b)))
        else: raise ValueError("invalid change export period")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"""SELECT s.name AS supplier_name,sp.external_id,sp.sku AS article,
              coalesce(h.field_name,h.field_group) AS field,h.old_value,h.new_value,h.changed_at,h.crawl_run_id AS run_id
              FROM entity_change_history h JOIN suppliers s ON s.id=h.supplier_id
              LEFT JOIN source_products sp ON sp.id=h.source_product_id
              WHERE {' AND '.join(clauses)} ORDER BY h.changed_at,h.id""",params)
            return list(cursor.fetchall())

    def product(self, supplier_code: str, external_id: str) -> dict | None:
        filters = ProductFilters(supplier=supplier_code, search=external_id, page_size=25)
        result = self.products(filters)
        item = next((row for row in result["items"] if str(row["external_id"]) == external_id), None)
        if not item:
            return None
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT raw_name,raw_value,raw_unit FROM product_properties WHERE source_product_id=%s ORDER BY position,id", (item["id"],))
            item["properties"] = list(cursor.fetchall())
            cursor.execute("SELECT media_type,source_url,title,is_primary FROM product_media WHERE source_product_id=%s ORDER BY media_type,position,id", (item["id"],))
            item["media"] = list(cursor.fetchall())
            cursor.execute("SELECT relation_type,to_source_product_id,raw_metadata FROM source_product_relations WHERE from_source_product_id=%s ORDER BY relation_type,id", (item["id"],))
            item["relations"] = list(cursor.fetchall())
            cursor.execute("SELECT description_text,description_html,raw_data FROM source_products WHERE id=%s", (item["id"],))
            item.update(cursor.fetchone())
            cursor.execute("SELECT field_name,observed_at,source_kind,source_path,normalized_by FROM field_observations WHERE source_product_id=%s ORDER BY field_name",(item["id"],))
            item["field_freshness"]=list(cursor.fetchall())
            if supplier_code in {"intervesp", "beka_mak", "beka_mak_tr", "kami"}:
                cursor.execute("""SELECT co.price_state AS commercial_price_state,
                    co.price AS commercial_price,co.currency AS commercial_currency,
                    co.availability_normalized AS commercial_availability,
                    co.observed_at AS commercial_observed_at,co.capture_id AS commercial_capture_id
                    FROM offer_commercial_observations co
                    JOIN source_products sp ON sp.id=co.source_product_id
                    WHERE sp.id=%s AND COALESCE(co.region_code,'')=
                        COALESCE(sp.raw_data->>'_commercial_projection_region','')
                    ORDER BY co.observed_at DESC,co.id DESC LIMIT 1""", (item["id"],))
                commercial = cursor.fetchone()
                if commercial:
                    item.update(commercial)
        return item

    def product_history(self, supplier_code: str, external_id: str) -> list[dict]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT h.changed_at,h.field_group,h.field_name,h.old_value,h.new_value,h.crawl_run_id,s.code AS supplier_code
              FROM entity_change_history h JOIN source_products sp ON sp.id=h.source_product_id
              JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s AND sp.external_id=%s
              ORDER BY h.changed_at DESC,h.id DESC""", (supplier_code,external_id))
            history = list(cursor.fetchall())
            if supplier_code in {"intervesp", "beka_mak", "beka_mak_tr", "kami"}:
                cursor.execute("""SELECT co.observed_at,co.price_state,co.price,co.currency,
                    co.availability_normalized,co.capture_id,s.code AS supplier_code
                    FROM offer_commercial_observations co
                    JOIN source_products sp ON sp.id=co.source_product_id
                    JOIN suppliers s ON s.id=sp.supplier_id
                    WHERE s.code=%s AND sp.external_id=%s
                    ORDER BY co.observed_at DESC,co.id DESC""", (supplier_code, external_id))
                for row in cursor.fetchall():
                    price_display = (f"{row['price']} {row['currency'] or ''}".strip()
                                     if row["price_state"] == "numeric_public" and row["price"] is not None
                                     else "Цена по запросу" if row["price_state"] == "price_on_request"
                                     else "Цена не указана")
                    history.append({"changed_at": row["observed_at"], "field_group": "commercial_observation",
                                    "field_name": "Коммерческое наблюдение", "old_value": None,
                                    "new_value": f"{price_display}; наличие: {row['availability_normalized'] or 'unknown'}",
                                    "crawl_run_id": None, "capture_id": row["capture_id"],
                                    "supplier_code": row["supplier_code"]})
                history.sort(key=lambda row: row["changed_at"], reverse=True)
            return history

    def runs(self, limit: int = 100) -> list[dict]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT cr.*,s.code AS supplier_code,s.name AS supplier_name,j.trigger_type
                FROM crawl_runs cr JOIN suppliers s ON s.id=cr.supplier_id
                LEFT JOIN crawl_jobs j ON j.crawl_run_id=cr.id
                ORDER BY cr.started_at DESC,cr.id DESC LIMIT %s""", (limit,))
            return list(cursor.fetchall())

    def run(self, run_id: int) -> dict | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT cr.*,s.name AS supplier_name FROM crawl_runs cr JOIN suppliers s ON s.id=cr.supplier_id WHERE cr.id=%s", (run_id,))
            row = cursor.fetchone()
            if row:
                cursor.execute("SELECT * FROM crawl_errors WHERE crawl_run_id=%s ORDER BY created_at,id", (run_id,))
                row["errors"] = list(cursor.fetchall())
        return row

    def run_progress(self, run_id: int) -> dict | None:
        row = self.run(run_id)
        if not row:
            return None
        metadata = row.get("metadata") or {}
        progress = dict(metadata.get("progress") or {}) if isinstance(metadata, dict) else {}
        progress.update({"run_id": run_id, "supplier_name": row.get("supplier_name"),
                         "run_status": row.get("status")})
        return progress

    def suppliers(self) -> list[dict]:
        return self.dashboard()

    def set_supplier_enabled(self, supplier_id: int, enabled: bool) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE suppliers SET enabled=%s,updated_at=now() WHERE id=%s", (enabled, supplier_id))

    def save_schedule(self, supplier_id: int, values: dict[str, Any]) -> None:
        rate = {key: values[key] for key in values if key in {
            "max_concurrency", "min_request_interval_seconds", "jitter_min_seconds", "jitter_max_seconds",
            "request_timeout_seconds", "max_attempts", "backoff_base_seconds", "backoff_max_seconds",
            "respect_retry_after", "consecutive_error_threshold", "cooldown_seconds", "max_run_duration_seconds",
        }}
        with self._connect() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute("UPDATE suppliers SET rate_settings=%s::jsonb,updated_at=now() WHERE id=%s", (json.dumps(rate), supplier_id))
            cursor.execute("""INSERT INTO supplier_schedules(supplier_id,enabled,cron_expression,timezone,crawl_type)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(supplier_id) DO UPDATE SET enabled=excluded.enabled,
                cron_expression=excluded.cron_expression,timezone=excluded.timezone,crawl_type=excluded.crawl_type,updated_at=now()""",
                (supplier_id, values["enabled"], values["cron_expression"] or None, values["timezone"], values["crawl_type"]))

    def save_data_quality_policy(self, supplier_id: int, values: dict[str, float]) -> None:
        from universal_supplier.stage6e import AnomalyThresholds
        policy=AnomalyThresholds(**values)
        payload={"count_ratio_min":policy.count_ratio_min,"fill_rate_drop":policy.fill_rate_drop,"fill_rate_min":policy.fill_rate_min}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE suppliers SET crawl_settings=jsonb_set(crawl_settings,'{data_quality_policy}',%s::jsonb,true),updated_at=now()
              WHERE id=%s""",(json.dumps(payload),supplier_id))

    def xml_canonical_snapshot(self, supplier: str = ""):
        from universal_supplier.yml_feed import FeedSnapshot, PostgresFeedStore
        import psycopg
        with psycopg.connect(**self.config.kwargs(), autocommit=True) as connection:
            snapshot = PostgresFeedStore(connection).load_snapshot()
        if supplier:
            selected = tuple(row for row in snapshot.selected_rows if row.supplier_code == supplier)
            snapshot = FeedSnapshot(selected, snapshot.not_managed_rows, {"selected": len(selected)}, snapshot.snapshot_timestamp)
        return snapshot

    def full_xml_summary(self, filters: ProductFilters) -> dict[str, int]:
        total = self.products(filters)["total"]
        counts = {"products": int(total), "properties": 0, "images": 0, "documents": 0,
                  "relations": 0, "options": 0, "matched": 0, "unmatched": 0}
        for row in self.iter_export_rows(filters):
            counts["properties"] += int(row.get("property_count") or 0)
            counts["images"] += int(row.get("image_count") or 0)
            counts["documents"] += int(row.get("document_count") or 0)
            counts["relations"] += int(row.get("relation_count") or 0)
            counts["options"] += int(row.get("option_count") or 0)
            counts["matched" if row.get("confirmed_sterbrust_id") else "unmatched"] += 1
        return counts

    def xml_canonical_summary(self, supplier: str = "") -> dict[str, int]:
        params = (supplier,) if supplier else ()
        with self._connect() as connection, connection.cursor() as cursor:
            if supplier:
                selected_scope = """EXISTS (
                    SELECT 1 FROM offers scoped_o
                    JOIN suppliers scoped_s ON scoped_s.id=scoped_o.supplier_id
                    WHERE scoped_o.id=cs.selected_offer_id AND scoped_s.code=%s
                )"""
                product_scope = """EXISTS (
                    SELECT 1 FROM source_products scoped_sp
                    JOIN suppliers scoped_s ON scoped_s.id=scoped_sp.supplier_id
                    WHERE scoped_sp.catalog_product_id=cs.catalog_product_id AND scoped_s.code=%s
                )"""
                cursor.execute(
                    f"""SELECT count(*) AS n FROM catalog_offer_selection cs
                        WHERE (cs.selection_status='selected' AND {selected_scope})
                           OR (cs.selection_status<>'selected' AND {product_scope})""",
                    (supplier, supplier),
                )
                considered = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"""SELECT count(*) AS n FROM catalog_offer_selection cs
                        WHERE cs.selection_status='selected' AND {selected_scope}""", params)
                selected = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"""SELECT count(*) AS n FROM catalog_offer_selection cs
                        WHERE cs.selection_status='no_eligible_offer' AND {product_scope}""", params)
                no_offer = int(cursor.fetchone()["n"])
            else:
                cursor.execute("SELECT count(*) AS n FROM catalog_offer_selection")
                considered = int(cursor.fetchone()["n"])
                cursor.execute("SELECT count(*) AS n FROM catalog_offer_selection WHERE selection_status='selected'")
                selected = int(cursor.fetchone()["n"])
                cursor.execute("SELECT count(*) AS n FROM catalog_offer_selection WHERE selection_status='no_eligible_offer'")
                no_offer = int(cursor.fetchone()["n"])
            if supplier:
                cursor.execute(
                    f"""SELECT count(*) AS n FROM product_identity_decisions pid
                        JOIN source_products sp ON sp.id=pid.source_product_id
                        JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s AND pid.decision='CONFLICT'""", params)
                conflict = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"""SELECT count(*) AS n FROM product_identity_decisions pid
                        JOIN source_products sp ON sp.id=pid.source_product_id
                        JOIN suppliers s ON s.id=sp.supplier_id
                        WHERE s.code=%s AND pid.decision IN ('REVIEW_EXISTING','INSUFFICIENT_IDENTITY')""", params)
                review = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"""SELECT count(*) AS n FROM product_identity_decisions pid
                        JOIN source_products sp ON sp.id=pid.source_product_id
                        JOIN suppliers s ON s.id=sp.supplier_id
                        WHERE s.code=%s AND pid.decision='SAFE_NEW_PRODUCT_CANDIDATE'""", params)
                safe_new = int(cursor.fetchone()["n"])
                cursor.execute(
                    f"""SELECT count(*) AS n FROM product_identity_decisions pid
                        JOIN source_products sp ON sp.id=pid.source_product_id
                        JOIN suppliers s ON s.id=sp.supplier_id
                        WHERE s.code=%s AND pid.decision='ACCESSORY_OR_COMPATIBILITY_ITEM'""", params)
                accessories = int(cursor.fetchone()["n"])
            else:
                cursor.execute("SELECT count(*) AS n FROM product_identity_decisions WHERE decision='CONFLICT'")
                conflict = int(cursor.fetchone()["n"])
                cursor.execute("SELECT count(*) AS n FROM product_identity_decisions WHERE decision IN ('REVIEW_EXISTING','INSUFFICIENT_IDENTITY')")
                review = int(cursor.fetchone()["n"])
                cursor.execute("SELECT count(*) AS n FROM product_identity_decisions WHERE decision='SAFE_NEW_PRODUCT_CANDIDATE'")
                safe_new = int(cursor.fetchone()["n"])
                cursor.execute("SELECT count(*) AS n FROM product_identity_decisions WHERE decision='ACCESSORY_OR_COMPATIBILITY_ITEM'")
                accessories = int(cursor.fetchone()["n"])
            selected_supplier_join = ""
            selected_supplier_where = ""
            selected_supplier_params = ()
            if supplier:
                selected_supplier_join = " JOIN suppliers selected_s ON selected_s.id=o.supplier_id"
                selected_supplier_where = " AND selected_s.code=%s"
                selected_supplier_params = params
            cursor.execute(
                "SELECT count(*) AS n FROM catalog_offer_selection cs "
                "JOIN offers o ON o.id=cs.selected_offer_id" + selected_supplier_join +
                " WHERE cs.selection_status='selected' AND (o.price IS NULL OR o.price<=0)" +
                selected_supplier_where,
                selected_supplier_params,
            )
            without_price = int(cursor.fetchone()["n"])
            cursor.execute(
                "SELECT count(*) AS n FROM catalog_offer_selection cs "
                "JOIN offers o ON o.id=cs.selected_offer_id" + selected_supplier_join +
                " LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=cs.catalog_product_id "
                "WHERE cs.selection_status='selected' "
                "AND (coalesce(sb.category_id,'')='' OR coalesce(sb.category_name,'')='')" +
                selected_supplier_where,
                selected_supplier_params,
            )
            without_category = int(cursor.fetchone()["n"])
        return {
            "considered": considered, "selected": selected, "no_selected_offer": no_offer,
            "blocked_by_conflict": conflict, "needs_review": review, "safe_new": safe_new,
            "accessories": accessories, "without_price": without_price, "without_category": without_category,
        }

    def save_missing_policy(self, supplier_id: int, values: dict[str, Any]) -> None:
        from universal_supplier.stage6e import MissingPolicy
        policy=MissingPolicy(warning_at=int(values["warning_at"]),candidate_at=int(values["candidate_at"]))
        payload={"warning_at":policy.warning_at,"candidate_at":policy.candidate_at}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE suppliers SET crawl_settings=jsonb_set(coalesce(crawl_settings,'{}'::jsonb),'{missing_policy}',%s::jsonb,true),updated_at=now()
              WHERE id=%s""",(json.dumps(payload),supplier_id))


class SnapshotControlPlaneStore:
    """Read-only packaged-data UI used for tests/screenshots without PostgreSQL."""

    def __init__(self, reports: Path) -> None:
        self.reports = reports
        self._rows = []
        for supplier, filename in (("optimum", "OPTIMUM_IDENTITY_AUDIT.csv"), ("partner_st", "PARTNER_ST_IDENTITY_REGRESSION.csv")):
            with (reports / filename).open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    row["supplier_code"] = supplier
                    row["supplier_name"] = "Optimum" if supplier == "optimum" else "Partner-ST"
                    row["external_id"] = row["supplier_external_id"]
                    row["name"] = row["source_name"]
                    row["brand_raw"] = row["brand"]
                    row["best_sterbrust_id"] = row["best_candidate"]
                    row["proposed_sterbrust_id"] = (
                        row["best_candidate"] or None if row.get("decision") == "REVIEW_EXISTING" else None
                    )
                    row["confirmed_sterbrust_id"] = (
                        row["best_candidate"] if row["decision"] in {"EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING"}
                        and row.get("auto_accepted") == "TRUE" else None
                    )
                    row["image_count"] = 0
                    self._rows.append(row)

    def health(self): return {"application": "ok", "postgresql": "demo_snapshot", "mode": "read_only"}

    def dashboard(self):
        return [{"id": i, "code": code, "name": name, "enabled": True, "products": sum(r["supplier_code"] == code for r in self._rows), "errors": 0, "last_status": "PACKAGED PASS", "last_crawl": "packaged snapshot", "job_status": None, "schedule_enabled": False, "cron_expression": None, "timezone": "UTC", "next_runs": [], "rate_settings": policy, "crawl_settings": {}} for i, (code, name, policy) in enumerate((("partner_st", "Partner-ST", {"max_concurrency": 4, "min_request_interval_seconds": .20}), ("optimum", "Optimum", {"max_concurrency": 3, "min_request_interval_seconds": .15})), 1)]

    def filter_options(self):
        return {"suppliers": [{"code": code, "name": name} for code, name in (("optimum", "Optimum"), ("partner_st", "Partner-ST"))], "brands": sorted({r["brand"] for r in self._rows if r["brand"]}), "decisions": DECISIONS, "kinds": sorted({r["product_kind"] for r in self._rows if r["product_kind"]})}

    def products(self, filters: ProductFilters):
        rows = self._rows
        for attr, key in (("supplier", "supplier_code"), ("brand", "brand"), ("decision", "decision"), ("product_kind", "product_kind")):
            value = getattr(filters, attr)
            if value: rows = [r for r in rows if r[key] == value]
        if filters.category: rows = [r for r in rows if filters.category.lower() in r["category"].lower()]
        if filters.search:
            needle = filters.search.lower()
            rows = [r for r in rows if any(needle in str(r.get(key, "")).lower() for key in ("external_id", "SKU", "name", "own_model", "reference_model", "best_sterbrust_id"))]
        rows = sorted(rows, key=lambda r: (r["supplier_code"], r["brand"], r["name"], int(r["external_id"])))
        total = len(rows); start = (filters.page - 1) * filters.page_size
        return {"items": rows[start:start + filters.page_size], "total": total, "page": filters.page, "page_size": filters.page_size, "pages": max(1, (total + filters.page_size - 1)//filters.page_size)}

    def product(self, supplier_code, external_id):
        item = next((dict(r) for r in self._rows if r["supplier_code"] == supplier_code and r["external_id"] == external_id), None)
        if item: item.update({"properties": [], "media": [], "relations": [], "field_freshness": [], "description_text": "Packaged completeness evidence", "price": "", "quantity": ""})
        return item

    def export_rows(self, filters):
        result=[]
        page=1
        while True:
            paged=ProductFilters(**{**{key:value for key,value in filters.__dict__.items() if key not in {"page","page_size"}}, "page":page, "page_size":100})
            batch=self.products(paged)
            for row in batch["items"]:
                item=dict(row); item.update(source_product_id=item.get("supplier_external_id"), article=item.get("SKU"), sku=item.get("SKU"), model=item.get("own_model") or item.get("reference_model"), source_url=item.get("source_url", ""), description_text=item.get("description", ""), property_count=0, document_count=0, relation_count=0, last_seen_at="", last_success_at="", old_price="", currency="", price_type="unknown", availability_normalized=item.get("availability_normalized", "unknown"), quantity="")
                result.append(item)
            if page >= batch["pages"] or not batch["items"]:
                break
            page += 1
        return result
    def change_rows(self, **kwargs): return []
    def export_details(self, source_product_ids): return {key:[] for key in ("properties","images","documents","relations","matches","decisions")}
    def xml_canonical_snapshot(self, supplier=""):
        from universal_supplier.yml_feed import FeedSnapshot
        from datetime import datetime, timezone
        return FeedSnapshot((), (), {"selected": 0}, datetime.now(timezone.utc))
    def xml_canonical_summary(self, supplier=""):
        return {"considered":0,"selected":0,"no_selected_offer":0,"blocked_by_conflict":0,"needs_review":0,"safe_new":0,"accessories":0,"excluded_total":0,"without_price":0,"without_url":0,"without_category":0,"blocked_by_identity":0,"invalid_selected_data":0}
    def product_history(self, supplier_code, external_id): return []

    def runs(self, limit=100): return []
    def run(self, run_id): return None
    def run_progress(self, run_id):
        if int(run_id) != 1:
            return None
        return {"run_id": 1, "supplier_name": "Partner-ST", "run_status": "running",
                "phase": "FETCHING_PRODUCTS", "phase_label_ru": "Загрузка товаров",
                "status": "running", "total": 100, "processed": 25, "succeeded": 24,
                "errors": 1, "percent": 25.0, "rate_per_second": 2.5, "eta_seconds": 30,
                "elapsed_seconds": 10, "updated_at": "2026-09-17T00:00:00+00:00"}
    def suppliers(self): return self.dashboard()
    def set_supplier_enabled(self, supplier_id, enabled): raise RuntimeError("snapshot mode is read-only")
    def save_schedule(self, supplier_id, values): raise RuntimeError("snapshot mode is read-only")
