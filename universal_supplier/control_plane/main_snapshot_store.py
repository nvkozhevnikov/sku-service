"""Fixed, read-only restored main-supplier snapshot for the local QA panel."""
from __future__ import annotations

import os
from universal_supplier.postgres import PostgresConfig
from .store import PostgresControlPlaneStore, ProductFilters

MAIN_SNAPSHOT_HOST = "127.0.0.1"
MAIN_SNAPSHOT_PORT = 55448
MAIN_SNAPSHOT_DATABASE = "main_supplier_snapshot"
MAIN_SNAPSHOT_ROLE = "main_snapshot_runtime"
MAIN_SNAPSHOT_SYSTEM_ID = 7690591088719854484


class ReadOnlyMainSnapshotStore(PostgresControlPlaneStore):
    read_only = True
    source_label = "Восстановленный исторический снимок: main_supplier_snapshot / 127.0.0.1:55448 / dump 2026-09-18"

    def _connect(self):
        import psycopg
        return psycopg.connect(**self.config.kwargs(), connect_timeout=3, options="-c default_transaction_read_only=on",
                               autocommit=True, row_factory=psycopg.rows.dict_row)

    def dashboard(self):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT s.id,s.code,s.name,s.enabled,s.base_url,
                count(DISTINCT sp.id) FILTER (WHERE sp.active) products,
                max(cr.started_at) last_crawl FROM suppliers s
                LEFT JOIN source_products sp ON sp.supplier_id=s.id
                LEFT JOIN crawl_runs cr ON cr.supplier_id=s.id
                GROUP BY s.id ORDER BY s.name,s.id""")
            rows = list(cursor.fetchall())
        for row in rows:
            row["data_source"] = self.source_label
            row["source_reason"] = "Восстановленный снимок, не LIVE и не предназначен для записи."
            row.update({"errors_30d":0,"last_status":"HISTORICAL_SNAPSHOT","last_run_id":None,
                        "last_crawl_metadata":{},"last_discovered_count":None,"last_new_count":None,
                        "last_changed_count":None,"last_error_count":None,"last_duration_seconds":None,
                        "job_status":None,"schedule_enabled":False,"cron_expression":None,"timezone":"UTC",
                        "next_runs":[],"rate_settings":{},"crawl_settings":{}})
        known = {row["code"] for row in rows}
        for code, name in (("intervesp", "Intervesp"), ("beka_mak", "Beka-Mak"),
                           ("beka_mak_tr", "Bekamak.com (справочник)")):
            if code not in known:
                rows.append({"id": {"intervesp": -3, "beka_mak": -4, "beka_mak_tr": -5}[code], "code": code, "name": name,
                             "enabled": False, "base_url": "", "products": None,
                             "errors_30d": 0, "last_run_id": None, "last_status": "SOURCE_NOT_CONNECTED",
                             "last_crawl": None, "last_crawl_metadata": {}, "last_discovered_count": None,
                             "last_new_count": None, "last_changed_count": None, "last_error_count": None,
                             "last_duration_seconds": None, "job_status": None, "schedule_enabled": False,
                             "cron_expression": None, "timezone": "UTC", "next_runs": [],
                             "rate_settings": {}, "crawl_settings": {},
                             "data_source": "Источник не подключён",
                             "source_reason": "Stage 4 QA PostgreSQL недоступна в текущем запуске."})
        return sorted(rows, key=lambda row: (str(row["name"]).lower(), int(row["id"])))

    def health(self) -> dict:
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute("""SELECT current_database() AS database, current_user AS role,
                    pg_is_in_recovery() AS recovery, system_identifier FROM pg_control_system()""")
                row = cursor.fetchone()
            identity_ok = (row["database"] == MAIN_SNAPSHOT_DATABASE and
                           row["role"] == MAIN_SNAPSHOT_ROLE and not row["recovery"] and
                           row["system_identifier"] == MAIN_SNAPSHOT_SYSTEM_ID)
            return {"application": "ok", "postgresql": "historical_read_only" if identity_ok else "error",
                    "database": row["database"], "role": row["role"], "recovery": row["recovery"],
                    "system_identifier": row["system_identifier"],
                    "source": self.source_label}
        except Exception as error:
            return {"application": "ok", "postgresql": "unavailable", "source": self.source_label,
                    "error": str(error)[:200]}

    def filter_options(self):
        return {"suppliers":[{"code":row["code"],"name":row["name"]} for row in self.dashboard()],
                "brands":[],"decisions":(),"kinds":[]}

    def products(self, filters: ProductFilters):
        clauses, params = ["sp.active"], []
        if filters.supplier: clauses.append("s.code=%s"); params.append(filters.supplier)
        if filters.brand: clauses.append("sp.brand_raw=%s"); params.append(filters.brand)
        if filters.search:
            clauses.append("(sp.external_id ILIKE %s OR sp.sku ILIKE %s OR sp.name ILIKE %s)")
            params.extend([f"%{filters.search}%"]*3)
        where=" AND ".join(clauses)
        offer="LEFT JOIN LATERAL (SELECT price,currency,availability_normalized FROM offers WHERE source_product_id=sp.id AND active ORDER BY id LIMIT 1) o ON true"
        if filters.availability: where += " AND o.availability_normalized=%s"; params.append(filters.availability)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) total FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id {offer} WHERE {where}",params); total=cursor.fetchone()["total"]
            cursor.execute(f"SELECT sp.id,sp.external_id,sp.sku,sp.name,sp.brand_raw,sp.source_url,s.code supplier_code,s.name supplier_name,o.price,o.currency,o.availability_normalized FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id {offer} WHERE {where} ORDER BY s.code,sp.id LIMIT %s OFFSET %s",[*params,filters.page_size,(filters.page-1)*filters.page_size]); items=list(cursor.fetchall())
        for row in items: row.update({"decision":None,"product_kind":None,"own_model":row.get("sku"),"reference_model":None,"best_sterbrust_id":None,"image_count":0})
        return {"items":items,"total":total,"page":filters.page,"page_size":filters.page_size,"pages":max(1,(total+filters.page_size-1)//filters.page_size)}

    def product(self, supplier_code: str, external_id: str) -> dict | None:
        """Read legacy snapshots defensively: newer optional evidence tables may not exist."""
        result = self.products(ProductFilters(supplier=supplier_code, search=external_id, page_size=25))
        item = next((row for row in result["items"] if str(row["external_id"]) == external_id), None)
        if not item:
            return None
        item.update({"properties": [], "media": [], "relations": [], "field_freshness": [],
                     "description_text": None, "description_html": None, "raw_data": None,
                     "data_source": self.source_label})
        queries = (
            ("properties", "SELECT raw_name,raw_value,raw_unit FROM product_properties WHERE source_product_id=%s ORDER BY position,id"),
            ("media", "SELECT media_type,source_url,title,is_primary FROM product_media WHERE source_product_id=%s ORDER BY media_type,position,id"),
            ("relations", "SELECT relation_type,to_source_product_id,raw_metadata FROM source_product_relations WHERE from_source_product_id=%s ORDER BY relation_type,id"),
            ("field_freshness", "SELECT field_name,observed_at,source_kind,source_path,normalized_by FROM field_observations WHERE source_product_id=%s ORDER BY field_name"),
        )
        with self._connect() as connection, connection.cursor() as cursor:
            for field, query in queries:
                try:
                    cursor.execute(query, (item["id"],))
                    item[field] = list(cursor.fetchall())
                except Exception:
                    # Snapshot schema is authoritative.  An absent optional table
                    # is represented as unavailable evidence, never fabricated.
                    connection.rollback()
            try:
                cursor.execute("SELECT description_text,description_html,raw_data FROM source_products WHERE id=%s", (item["id"],))
                row = cursor.fetchone()
                if row:
                    item.update(row)
            except Exception:
                connection.rollback()
        return item

    def product_history(self, supplier_code: str, external_id: str) -> list[dict]:
        try:
            return super().product_history(supplier_code, external_id)
        except Exception:
            return []

    def runs(self, limit: int = 100) -> list[dict]:
        """Legacy dump predates crawl_jobs; show persisted crawl_runs only."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT cr.*,s.code AS supplier_code,s.name AS supplier_name,
                NULL::text AS trigger_type FROM crawl_runs cr
                JOIN suppliers s ON s.id=cr.supplier_id
                ORDER BY cr.started_at DESC,cr.id DESC LIMIT %s""", (limit,))
            return list(cursor.fetchall())

    def _blocked(self, *_args, **_kwargs):
        raise RuntimeError("Historical snapshot is read-only in this panel.")
    set_supplier_enabled = save_schedule = save_data_quality_policy = save_missing_policy = _blocked


def main_snapshot_store_from_env():
    if os.environ.get("COMMERCIAL_QA_MAIN_SNAPSHOT_SOURCE", "").lower() != "restored_snapshot":
        return None
    return ReadOnlyMainSnapshotStore(PostgresConfig(MAIN_SNAPSHOT_HOST, MAIN_SNAPSHOT_PORT,
        MAIN_SNAPSHOT_DATABASE, MAIN_SNAPSHOT_ROLE, None, "disable"))
