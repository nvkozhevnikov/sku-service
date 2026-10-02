"""Explicit, read-only presentation adapter for the portable Stage 4 QA DB.

This is intentionally not a general database selector: all connection identity
values are fixed to the approved portable QA instance.  The adapter never
merges local source-product IDs with another database and blocks every write.
"""
from __future__ import annotations

import os
from typing import Any

from universal_supplier.postgres import PostgresConfig

from .store import PostgresControlPlaneStore, ProductFilters


STAGE4_HOST = "127.0.0.1"
STAGE4_PORT = 55447
STAGE4_DATABASE = "stage4_commercial_qa"
STAGE4_ROLE = "stage4_runtime"
STAGE4_SYSTEM_ID = 7689025282387590508


class ReadOnlyStage4QaStore(PostgresControlPlaneStore):
    """Expose only the approved QA snapshot; all SQL transactions are read-only."""

    read_only = True
    source_label = "Изолированная QA PostgreSQL: stage4_commercial_qa / 127.0.0.1:55447"

    def _connect(self):
        import psycopg
        return psycopg.connect(**self.config.kwargs(), connect_timeout=3, options="-c default_transaction_read_only=on",
                               autocommit=True, row_factory=psycopg.rows.dict_row)

    @staticmethod
    def _unavailable_row(code: str, name: str, reason: str) -> dict[str, Any]:
        return {"id": {"partner_st": -1, "optimum": -2, "intervesp": -3,
                       "beka_mak": -4, "beka_mak_tr": -5}.get(code, -99), "code": code, "name": name,
                "enabled": False, "base_url": "", "products": None, "errors_30d": 0,
                "last_run_id": None, "last_status": "SOURCE_NOT_CONNECTED", "last_crawl": None,
                "last_crawl_metadata": {}, "last_discovered_count": None, "last_new_count": None,
                "last_changed_count": None, "last_error_count": None, "last_duration_seconds": None,
                "job_status": None, "schedule_enabled": False, "cron_expression": None,
                "timezone": "UTC", "next_runs": [], "rate_settings": {}, "crawl_settings": {},
                "data_source": "Источник не подключён", "source_reason": reason}

    def _supplement_sources(self, rows: list[dict]) -> list[dict]:
        for row in rows:
            row["data_source"] = self.source_label
            latest = row.get("latest_capture_at")
            row["source_reason"] = ("Только чтение; supplier и offers отключены. "
                                    + (f"Последний HTTP capture: {latest}." if latest else "HTTP capture пока нет."))
        known = {str(row.get("code")) for row in rows}
        unavailable = "В текущем запуске не найден разрешённый read-only источник с историческими данными."
        if "partner_st" not in known:
            rows.append(self._unavailable_row("partner_st", "Partner-ST", unavailable))
        if "optimum" not in known:
            rows.append(self._unavailable_row("optimum", "Optimum", unavailable))
        return sorted(rows, key=lambda row: (str(row["name"]).lower(), int(row["id"])))

    def health(self) -> dict:
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute("""SELECT current_database() AS database, current_user AS role,
                    pg_is_in_recovery() AS recovery, system_identifier FROM pg_control_system()""")
                row = cursor.fetchone()
            identity_ok = (row["database"] == STAGE4_DATABASE and row["role"] == STAGE4_ROLE
                           and not row["recovery"] and row["system_identifier"] == STAGE4_SYSTEM_ID)
            return {"application": "ok", "postgresql": "qa_read_only" if identity_ok else "error",
                    "database": row["database"], "role": row["role"], "recovery": row["recovery"],
                    "system_identifier": row["system_identifier"],
                    "source": self.source_label}
        except Exception as error:
            return {"application": "ok", "postgresql": "unavailable", "source": self.source_label,
                    "error": str(error)[:200]}

    def dashboard(self) -> list[dict]:
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute("""SELECT s.id,s.code,s.name,s.base_url,s.enabled,s.rate_settings,s.crawl_settings,
                       count(sp.id) FILTER (WHERE sp.active) AS products,
                       max(sp.last_success_at) AS latest_capture_at
                  FROM suppliers s LEFT JOIN source_products sp ON sp.supplier_id=s.id
                  GROUP BY s.id ORDER BY s.name,s.id""")
                rows = list(cursor.fetchall())
                cursor.execute("""SELECT s.code, count(co.id) AS commercial_observations,
                    count(*) FILTER (WHERE co.price_state='numeric_public') AS numeric_price_observations,
                    count(*) FILTER (WHERE co.price_state='price_on_request') AS on_request_observations
                  FROM suppliers s LEFT JOIN offer_commercial_observations co ON co.supplier_id=s.id GROUP BY s.code""")
                commercial = {row["code"]: row for row in cursor.fetchall()}
            for row in rows:
                row.update(commercial.get(row["code"], {}))
                row.update({"errors_30d": 0, "last_run_id": None, "last_status": None,
                            "last_crawl": None, "last_crawl_metadata": {},
                            "last_discovered_count": row["products"], "last_new_count": None,
                            "last_changed_count": None, "last_error_count": None,
                            "last_duration_seconds": None, "job_status": None,
                            "schedule_enabled": False, "cron_expression": None,
                            "timezone": "UTC", "next_runs": []})
            return self._supplement_sources(rows)
        except Exception as error:
            reason = f"QA PostgreSQL недоступна: {str(error)[:160]}"
            return self._supplement_sources([
                self._unavailable_row("intervesp", "Intervesp", reason),
                self._unavailable_row("beka_mak", "Beka-Mak", reason),
                self._unavailable_row("beka_mak_tr", "Bekamak.com (справочник)", reason),
            ])

    def suppliers(self) -> list[dict]:
        return self.dashboard()

    def filter_options(self) -> dict:
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT code,name FROM suppliers ORDER BY name")
                suppliers = list(cursor.fetchall())
                cursor.execute("SELECT DISTINCT brand_raw AS value FROM source_products "
                               "WHERE brand_raw IS NOT NULL AND btrim(brand_raw)<>'' ORDER BY value")
                brands = [row["value"] for row in cursor.fetchall()]
            return {"suppliers": suppliers, "brands": brands, "decisions": (), "kinds": []}
        except Exception:
            return {"suppliers": [], "brands": [], "decisions": (), "kinds": []}

    def products(self, filters: ProductFilters) -> dict:
        try:
            # The QA runtime role intentionally has no access to canonical
            # identity tables.  Query its own commercial namespace directly;
            # do not turn inaccessible canonical data into fake empty rows.
            clauses, params = ["sp.active"], []
            if filters.supplier: clauses.append("s.code=%s"); params.append(filters.supplier)
            if filters.brand: clauses.append("sp.brand_raw=%s"); params.append(filters.brand)
            if filters.search:
                clauses.append("(sp.external_id ILIKE %s OR sp.sku ILIKE %s OR sp.name ILIKE %s)")
                params.extend([f"%{filters.search}%"] * 3)
            where = " AND ".join(clauses)
            latest = """LEFT JOIN LATERAL (SELECT price_state,price,old_price,currency,availability_normalized,
                observed_at,region_code,capture_id FROM offer_commercial_observations co
                WHERE co.source_product_id=sp.id
                  AND COALESCE(co.region_code, '') = COALESCE(sp.raw_data ->> '_commercial_projection_region', '')
                ORDER BY observed_at DESC,id DESC LIMIT 1) co ON true"""
            if filters.availability: where += " AND co.availability_normalized=%s"; params.append(filters.availability)
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT count(*) AS total FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id {latest} WHERE {where}", params)
                total = cursor.fetchone()["total"]
                cursor.execute(f"""SELECT sp.id,sp.external_id,sp.sku,sp.name,sp.brand_raw,sp.source_url,
                    s.code AS supplier_code,s.name AS supplier_name,co.price_state,co.price,co.old_price,co.currency,
                    co.availability_normalized,co.observed_at,co.region_code,co.capture_id
                    FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id {latest} WHERE {where}
                    ORDER BY s.code,sp.name,sp.id LIMIT %s OFFSET %s""", [*params, filters.page_size, (filters.page-1)*filters.page_size])
                items = list(cursor.fetchall())
            for item in items:
                item.update({"decision": None, "product_kind": None, "own_model": item.get("sku"),
                             "reference_model": None, "best_sterbrust_id": None, "image_count": 0})
            return {"items": items, "total": total, "page": filters.page, "page_size": filters.page_size,
                    "pages": max(1, (total + filters.page_size - 1)//filters.page_size)}
        except Exception:
            return {"items": [], "total": 0, "page": filters.page, "page_size": filters.page_size, "pages": 0,
                    "source_unavailable": True}

    def product_history(self, supplier_code: str, external_id: str) -> list[dict]:
        """Commercial captures are history, not invented crawl-run history."""
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT co.observed_at,co.price_state,co.price,co.old_price,co.currency,
                                     co.availability_normalized,co.capture_id,co.region_code,s.code AS supplier_code
                              FROM offer_commercial_observations co
                              JOIN source_products sp ON sp.id=co.source_product_id
                              JOIN suppliers s ON s.id=sp.supplier_id
                              WHERE s.code=%s AND sp.external_id=%s
                              ORDER BY co.observed_at DESC,co.id DESC""", (supplier_code, external_id))
            return [{"changed_at": row["observed_at"], "field_group": "commercial_observation",
                     "field_name": row["price_state"], "old_value": None,
                     "new_value": " ".join(str(value) for value in (row["price"], row["currency"], row["availability_normalized"]) if value not in (None, "")),
                     "crawl_run_id": row["capture_id"], "supplier_code": row["supplier_code"]}
                    for row in cursor.fetchall()]

    def product(self, supplier_code: str, external_id: str) -> dict | None:
        # Runtime role deliberately cannot read legacy reconciliation tables.
        # The QA detail view must use only its narrow commercial namespace.
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT sp.id,sp.external_id,sp.sku,sp.name,sp.source_url,sp.canonical_url,
                   sp.brand_raw,sp.manufacturer_raw,sp.description_text,sp.raw_data,
                   s.code AS supplier_code,s.name AS supplier_name,o.active AS offer_active,
                   co.price_state AS commercial_price_state,co.price AS commercial_price,
                   co.old_price AS commercial_old_price,co.currency AS commercial_currency,
                   co.availability_normalized AS commercial_availability,
                   co.observed_at AS commercial_observed_at,co.region_code AS commercial_region,
                   co.capture_id AS commercial_capture_id
              FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
              JOIN offers o ON o.source_product_id=sp.id AND o.supplier_id=s.id AND o.offer_kind='default'
              LEFT JOIN LATERAL (
                  SELECT co.* FROM offer_commercial_observations co
                  WHERE co.source_product_id=sp.id AND co.supplier_id=s.id
                    AND COALESCE(co.region_code, '') = COALESCE(sp.raw_data ->> '_commercial_projection_region', '')
                  ORDER BY co.observed_at DESC,co.id DESC LIMIT 1
              ) co ON true
              WHERE s.code=%s AND sp.external_id=%s""", (supplier_code, external_id))
            item = cursor.fetchone()
        if not item:
            return None
        raw_data = item.get("raw_data") or {}
        content = raw_data.get("source_content") or {}
        item["data_source"] = self.source_label
        item["category"] = content.get("source_category") or ""
        item["own_model"] = (raw_data.get("source_identity") or {}).get("raw_model") or item.get("sku")
        item["reference_model"] = None
        item["product_kind"] = None
        item["best_sterbrust_id"] = None
        item["decision"] = None
        item["properties"] = [{"raw_name": pair[0], "raw_value": pair[1], "raw_unit": ""}
                              for pair in content.get("technical_properties", [])
                              if isinstance(pair, list) and len(pair) >= 2]
        item["media"] = [{"source_url": url, "title": "", "media_type": "image", "is_primary": index == 0}
                         for index, url in enumerate(content.get("source_images", []))]
        item["relations"] = []
        item["field_freshness"] = []
        item["price"] = item.get("commercial_price")
        item["currency"] = item.get("commercial_currency")
        item["availability_normalized"] = item.get("commercial_availability") or "unknown"
        return item

    def runs(self, limit: int = 100) -> list[dict]:
        # Stage 5D commercial writes did not create crawl_runs; do not invent them.
        return []

    def run(self, run_id: int) -> dict | None:
        return None

    def run_progress(self, run_id: int) -> dict | None:
        return None

    def _write_blocked(self, *_args, **_kwargs):
        raise RuntimeError("Stage 4 QA source is read-only in this panel.")

    set_supplier_enabled = _write_blocked
    save_schedule = _write_blocked
    save_data_quality_policy = _write_blocked
    save_missing_policy = _write_blocked


def stage4_qa_store_from_env() -> ReadOnlyStage4QaStore | None:
    """Return the QA store only after an explicit, fixed-target opt-in."""
    if os.environ.get("COMMERCIAL_QA_DATA_SOURCE", "").lower() != "stage4_readonly":
        return None
    password = os.environ.get("COMMERCIAL_QA_STAGE4_PASSWORD") or None
    config = PostgresConfig(STAGE4_HOST, STAGE4_PORT, STAGE4_DATABASE, STAGE4_ROLE, password, "disable")
    return ReadOnlyStage4QaStore(config)
