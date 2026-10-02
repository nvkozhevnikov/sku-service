"""Read-only, fixed-target SQL verification for the three-source local QA run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


IDENTITY = ("stage4_commercial_qa", "stage4_runtime", "17.11", False,
            7689025282387590508)
SOURCES = ("intervesp", "beka_mak", "beka_mak_tr")


def verify() -> dict:
    import psycopg

    with psycopg.connect(host="127.0.0.1", port=55447, dbname=IDENTITY[0],
                         user=IDENTITY[1], sslmode="disable", autocommit=True,
                         options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                           "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
            found = cursor.fetchone()
            if found != IDENTITY:
                raise RuntimeError("Stage 4 QA identity changed; no further SQL attempted")
            cursor.execute("SELECT code,enabled FROM suppliers WHERE code=ANY(%s) ORDER BY code", (list(SOURCES),))
            suppliers = {code: enabled for code, enabled in cursor.fetchall()}
            if set(suppliers) != set(SOURCES):
                raise RuntimeError("one or more expected QA sources are missing")
            counts = {}
            for table in ("source_products", "offers", "supplier_http_captures",
                          "offer_commercial_observations", "product_matches", "catalog_products"):
                cursor.execute(f"SELECT count(*) FROM {table}")
                counts[table] = cursor.fetchone()[0]
            cursor.execute("""SELECT s.code,
                           (SELECT count(*) FROM source_products sp WHERE sp.supplier_id=s.id),
                           (SELECT count(*) FROM offers o WHERE o.supplier_id=s.id),
                           (SELECT count(*) FROM supplier_http_captures h WHERE h.supplier_id=s.id),
                           (SELECT count(*) FROM offer_commercial_observations co WHERE co.supplier_id=s.id)
                           FROM suppliers s
                           WHERE s.code=ANY(%s) ORDER BY s.code""", (list(SOURCES),))
            by_source = {code: {"source_products": sp, "offers": offers,
                                "captures": captures, "observations": observations}
                         for code, sp, offers, captures, observations in cursor.fetchall()}
            checks = {}
            for name, query in {
                "duplicate_source_products": """SELECT count(*) FROM (SELECT supplier_id,external_id
                    FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1) d""",
                "duplicate_offers": """SELECT count(*) FROM (SELECT supplier_id,source_product_id,offer_kind
                    FROM offers GROUP BY supplier_id,source_product_id,offer_kind
                    HAVING count(*)>1) d""",
                "active_offers": "SELECT count(*) FROM offers WHERE active",
                "canonical_links": "SELECT count(*) FROM source_products WHERE catalog_product_id IS NOT NULL",
            }.items():
                cursor.execute(query)
                checks[name] = cursor.fetchone()[0]
            cursor.execute("""SELECT s.code,co.price_state,count(*)
                           FROM suppliers s JOIN source_products sp ON sp.supplier_id=s.id
                           JOIN LATERAL (SELECT co.price_state FROM offer_commercial_observations co
                               WHERE co.supplier_id=s.id AND co.source_product_id=sp.id
                                 AND COALESCE(co.region_code,'')=COALESCE(sp.raw_data->>'_commercial_projection_region','')
                               ORDER BY co.observed_at DESC,co.id DESC LIMIT 1) co ON true
                           WHERE s.code=ANY(%s) GROUP BY s.code,co.price_state
                           ORDER BY s.code,co.price_state""", (list(SOURCES),))
            price_states: dict[str, dict[str, int]] = {source: {} for source in SOURCES}
            for code, state, count in cursor.fetchall():
                price_states[code][state] = count
    return {"checked_at": datetime.now(timezone.utc).isoformat(),
            "identity": {"database": found[0], "role": found[1], "version": found[2],
                         "recovery": found[3], "system_identifier": found[4]},
            "suppliers_enabled": suppliers, "counts": counts, "by_source": by_source,
            "checks": checks, "current_price_states": price_states}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
