"""Read-only post-RUN2 reconciliation of the dedicated local RC database."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from universal_supplier.full_supplier_collection import FullManifest


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("intervesp", "beka_mak", "beka_mak_tr")
EXPECTED_ID = ("universal_supplier_server", "rc_readonly", "17.11", False, 7691270601420084116)


def verify(manifest_path: Path, run1_summary_path: Path, run2_report_path: Path) -> dict:
    manifest = FullManifest.from_jsonable(json.loads(manifest_path.read_text(encoding="utf-8")))
    before = json.loads(run1_summary_path.read_text(encoding="utf-8"))["counts_after"]["counts"]
    report = json.loads(run2_report_path.read_text(encoding="utf-8"))
    if (report.get("manifest_sha256") != manifest.sha256 or report.get("dry_run") is not False
            or report.get("aborted_reason") or report.get("blocked_sources")
            or len(report.get("rows", [])) != len(manifest.candidates)):
        raise RuntimeError("RUN2 is not one complete pinned-manifest write run")
    candidate_keys = {(item.source, item.url): item.expected_model for item in manifest.candidates}
    seen = set()
    by_source: dict[str, Counter] = defaultdict(Counter)
    for row in report["rows"]:
        key = (row.get("source"), row.get("url"))
        if key in seen or key not in candidate_keys or row.get("expected_model") != candidate_keys[key]:
            raise RuntimeError("RUN2 repeats a candidate or diverges from the manifest")
        seen.add(key)
        by_source[row["source"]][row["status"]] += 1
        if row.get("observation_created"):
            by_source[row["source"]]["observation_created"] += 1
        if row.get("exact_noop"):
            by_source[row["source"]]["exact_noop"] += 1
    if seen != set(candidate_keys):
        raise RuntimeError("RUN2 did not cover every pinned candidate")
    with psycopg.connect(host="127.0.0.1", port=55449, dbname="universal_supplier_server",
                         user="rc_readonly", options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                           "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
            if tuple(cursor.fetchone()) != EXPECTED_ID:
                raise RuntimeError("RC PostgreSQL identity changed")
            cursor.execute("""SELECT s.code,s.enabled,
                (SELECT count(*) FROM source_products sp WHERE sp.supplier_id=s.id),
                (SELECT count(*) FROM offers o WHERE o.supplier_id=s.id),
                (SELECT count(*) FROM supplier_http_captures c WHERE c.supplier_id=s.id),
                (SELECT count(*) FROM offer_commercial_observations x WHERE x.supplier_id=s.id),
                (SELECT count(*) FROM offers o WHERE o.supplier_id=s.id AND o.active),
                (SELECT count(*) FROM source_products sp WHERE sp.supplier_id=s.id AND sp.catalog_product_id IS NOT NULL)
                FROM suppliers s ORDER BY s.code""")
            counts = {code: {"enabled": enabled, "source_products": products, "offers": offers,
                             "captures": captures, "observations": observations,
                             "active_offers": active, "canonical_links": links}
                      for code, enabled, products, offers, captures, observations, active, links in cursor.fetchall()}
            cursor.execute("SELECT count(*) FROM (SELECT supplier_id,external_id FROM source_products "
                           "GROUP BY supplier_id,external_id HAVING count(*)>1) d")
            duplicate_products = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM (SELECT supplier_id,external_offer_id FROM offers "
                           "GROUP BY supplier_id,external_offer_id HAVING count(*)>1) d")
            duplicate_offers = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM product_matches pm JOIN source_products sp "
                           "ON sp.id=pm.source_product_id JOIN suppliers s ON s.id=sp.supplier_id "
                           "WHERE s.code IN ('intervesp','beka_mak','beka_mak_tr')")
            commercial_matches = cursor.fetchone()[0]
            global_counts = {}
            for table in ("source_products", "offers", "supplier_http_captures",
                          "offer_commercial_observations", "product_matches", "catalog_products",
                          "catalog_offer_selection"):
                cursor.execute(f"SELECT count(*) FROM {table}")
                global_counts[table] = cursor.fetchone()[0]
            cursor.execute("""SELECT s.code,co.price_state,count(*) FROM source_products sp
                JOIN suppliers s ON s.id=sp.supplier_id
                LEFT JOIN LATERAL (SELECT price_state FROM offer_commercial_observations co
                    WHERE co.source_product_id=sp.id AND COALESCE(co.region_code,'')=
                    COALESCE(sp.raw_data->>'_commercial_projection_region','')
                    ORDER BY co.observed_at DESC,co.id DESC LIMIT 1) co ON true
                WHERE s.code IN ('intervesp','beka_mak','beka_mak_tr')
                GROUP BY s.code,co.price_state""")
            price_states: dict[str, dict[str, int]] = defaultdict(dict)
            for code, state, count in cursor.fetchall():
                price_states[code][state or "no_observation"] = count
    if duplicate_products or duplicate_offers or commercial_matches:
        raise RuntimeError("commercial duplicate or automatic match after RUN2")
    if any(counts[code]["enabled"] or counts[code]["active_offers"] or counts[code]["canonical_links"]
           for code in SOURCES):
        raise RuntimeError("commercial supplier/offer/canonical safety invariant changed")
    if (any(global_counts[table] != before[table] for table in
            ("catalog_products", "product_matches")) or
            global_counts["catalog_offer_selection"] != 121):
        raise RuntimeError("canonical products, prior matches or selections changed during RUN2")
    created = sum(by_source[code]["observation_created"] for code in SOURCES)
    if (global_counts["offer_commercial_observations"] - before["offer_commercial_observations"] != created
            or global_counts["supplier_http_captures"] - before["supplier_http_captures"] != created):
        raise RuntimeError("new capture/observation counts differ from committed RUN2 rows")
    if sum(counts[code]["source_products"] for code in SOURCES) != (
            global_counts["source_products"] - counts["partner_st"]["source_products"]
            - counts["optimum"]["source_products"]):
        raise RuntimeError("unaccounted source products outside the five namespaces")
    return {"evidence_level": "LIVE_READ_ONLY_SQL", "manifest_sha256": manifest.sha256,
            "report_sha256": hashlib.sha256(run2_report_path.read_bytes()).hexdigest(),
            "status_by_source": {code: dict(by_source[code]) for code in SOURCES},
            "before": before, "after": global_counts, "source_counts": counts,
            "current_price_states": dict(price_states), "duplicate_products": duplicate_products,
            "duplicate_offers": duplicate_offers, "commercial_matches": commercial_matches,
            "verified": True}


def main() -> None:
    output = ROOT / "reports" / "RC_LOCAL" / "RUN2_VERIFIED.json"
    result = verify(ROOT / "reports" / "WORKING_V1_2026-09-29" / "full_candidate_manifest.json",
                    ROOT / "reports" / "RC_LOCAL" / "RUN1_INTERVESP_RESUME" / "summary.json",
                    ROOT / "reports" / "RC_LOCAL" / "RUN2_ALL" / "report.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": result["verified"], "status_by_source": result["status_by_source"],
                      "after": result["after"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
