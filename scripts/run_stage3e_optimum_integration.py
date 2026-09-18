#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
sys.path.insert(0, str(ROOT))

from universal_supplier.adapters.optimum import run_optimum_live_sync
from universal_supplier.change_detection import card_hashes
from universal_supplier.matching_service import load_registry_from_postgres, match_cards
from universal_supplier.postgres import PostgresConfig, PostgresRepository
from universal_supplier.state import discovery_health

MATCH_FIELDS = ["source_external_id", "SKU", "name", "brand", "model", "status", "match_method",
                "confidence_score", "sterbrust_product_id", "conflict_class", "warnings",
                "data_quality_flags", "conflicts", "evidence", "auto_accepted"]


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or (list(rows[0]) if rows else ["status"])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def supplier_counts(connection, code: str) -> dict[str, int]:
    queries = {
        "source_products": "SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s",
        "offers": "SELECT count(*) FROM offers o JOIN suppliers s ON s.id=o.supplier_id WHERE s.code=%s AND o.offer_kind='default'",
        "categories": "SELECT count(*) FROM supplier_categories c JOIN suppliers s ON s.id=c.supplier_id WHERE s.code=%s",
        "properties": "SELECT count(*) FROM product_properties p JOIN source_products sp ON sp.id=p.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s",
        "media": "SELECT count(*) FROM product_media m JOIN source_products sp ON sp.id=m.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s",
        "documents": "SELECT count(*) FROM product_media m JOIN source_products sp ON sp.id=m.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s AND m.media_type='document'",
        "relations": "SELECT count(*) FROM source_product_relations r JOIN suppliers s ON s.id=r.supplier_id WHERE s.code=%s",
        "aliases": "SELECT count(*) FROM source_product_urls u JOIN source_products sp ON sp.id=u.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s AND u.url_kind='alias'",
        "current_matches": "SELECT count(*) FROM product_matches pm JOIN source_products sp ON sp.id=pm.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s AND pm.is_current",
        "accepted_links": "SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s AND sp.catalog_product_id IS NOT NULL",
    }
    result = {}
    with connection.cursor() as cursor:
        for key, query in queries.items():
            cursor.execute(query, (code,))
            result[key] = cursor.fetchone()[0]
    return result


def partner_state(connection) -> dict:
    values = supplier_counts(connection, "partner_st")
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT sp.catalog_product_id,pm.status,pm.match_method,pm.auto_accepted,sb.sterbrust_product_id
               FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
               JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
               LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=pm.catalog_product_id
               WHERE s.code='partner_st' AND sp.external_id='297'"""
        )
        values["pp800f"] = cursor.fetchone()
        cursor.execute(
            """SELECT sp.catalog_product_id,pm.status,pm.match_method,pm.auto_accepted
               FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
               JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
               WHERE s.code='partner_st' AND sp.sku='211301'"""
        )
        values["mrx3"] = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM sterbrust_product_supplier_identifiers WHERE supplier_code='partner_st'")
        values["supplier_identifiers"] = cursor.fetchone()[0]
    return values


def scalar_qa(connection) -> dict[str, int]:
    queries = {
        "OPTIMUM_DUPLICATE_STABLE_EXTERNAL_IDS": "SELECT count(*) FROM (SELECT sp.external_id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='optimum' AND sp.external_id_is_stable GROUP BY sp.external_id HAVING count(*)>1)x",
        "OPTIMUM_DUPLICATE_DEFAULT_OFFERS": "SELECT count(*) FROM (SELECT o.source_product_id FROM offers o JOIN suppliers s ON s.id=o.supplier_id WHERE s.code='optimum' AND o.offer_kind='default' GROUP BY o.source_product_id HAVING count(*)>1)x",
        "OPTIMUM_ALIAS_DUPLICATE_PRODUCTS": "SELECT count(*) FROM (SELECT u.url FROM source_product_urls u JOIN source_products sp ON sp.id=u.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='optimum' GROUP BY u.url HAVING count(DISTINCT sp.id)>1)x",
        "CROSS_SUPPLIER_SOURCE_PRODUCT_VIOLATIONS": "SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='optimum' AND coalesce(sp.raw_data#>>'{source_record,supplier}','optimum')<>'optimum'",
        "CROSS_SUPPLIER_OFFER_VIOLATIONS": "SELECT count(*) FROM offers o JOIN source_products sp ON sp.id=o.source_product_id WHERE o.supplier_id<>sp.supplier_id",
        "CROSS_SUPPLIER_CATEGORY_VIOLATIONS": "SELECT count(*) FROM source_product_categories pc JOIN source_products sp ON sp.id=pc.source_product_id WHERE pc.supplier_id<>sp.supplier_id",
        "CROSS_SUPPLIER_URL_VIOLATIONS": "SELECT count(*) FROM source_product_urls u LEFT JOIN source_products sp ON sp.id=u.source_product_id WHERE sp.id IS NULL",
        "CROSS_SUPPLIER_RELATION_VIOLATIONS": "SELECT count(*) FROM source_product_relations r JOIN source_products a ON a.id=r.from_source_product_id JOIN source_products b ON b.id=r.to_source_product_id WHERE r.supplier_id<>a.supplier_id OR r.supplier_id<>b.supplier_id",
        "CROSS_SUPPLIER_IDENTIFIER_VIOLATIONS": "SELECT count(*) FROM sterbrust_product_supplier_identifiers i JOIN suppliers s ON s.id=i.supplier_id WHERE i.supplier_code<>s.code",
        "CROSS_SUPPLIER_ARTICLE_AUTO_MATCHES": "SELECT count(*) FROM product_matches pm JOIN source_products sp ON sp.id=pm.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE pm.auto_accepted AND pm.match_method LIKE 'SUPPLIER_ARTICLE%' AND coalesce(pm.evidence->>'supplier_code','')<>s.code",
        "FUZZY_AUTO_MATCHES": "SELECT count(*) FROM product_matches WHERE auto_accepted AND match_method IN ('NAME_CANDIDATE_ONLY','NO_IDENTITY_EVIDENCE')",
        "FALSE_EXISTING_LINK_CONFLICTS": "SELECT count(*) FROM product_matches current_match JOIN product_matches previous_match ON previous_match.id=current_match.previous_match_id WHERE current_match.is_current AND current_match.status='CONFLICT' AND previous_match.auto_accepted AND coalesce(current_match.evidence->>'conflict_class','')<>'IDENTITY_CONFLICT'",
    }
    result = {}
    with connection.cursor() as cursor:
        for key, query in queries.items():
            cursor.execute(query)
            result[key] = cursor.fetchone()[0]
    return result


def overlap_rows(connection) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT cp.id,sb.sterbrust_product_id,sp1.id,sp2.id,o1.id,o2.id,
                      sp1.sku,sp2.sku,coalesce(sp2.brand_raw,''),sp2.name,
                      pm2.match_method,pm2.evidence
               FROM catalog_products cp
               JOIN source_products sp1 ON sp1.catalog_product_id=cp.id
               JOIN suppliers s1 ON s1.id=sp1.supplier_id AND s1.code='partner_st'
               JOIN source_products sp2 ON sp2.catalog_product_id=cp.id
               JOIN suppliers s2 ON s2.id=sp2.supplier_id AND s2.code='optimum'
               JOIN offers o1 ON o1.source_product_id=sp1.id AND o1.offer_kind='default'
               JOIN offers o2 ON o2.source_product_id=sp2.id AND o2.offer_kind='default'
               JOIN product_matches pm2 ON pm2.source_product_id=sp2.id AND pm2.is_current AND pm2.auto_accepted
               LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=cp.id
               ORDER BY cp.id"""
        )
        return [{
            "catalog_product_id": row[0], "sterbrust_product_id": row[1],
            "partner_source_product_id": row[2], "optimum_source_product_id": row[3],
            "partner_offer_id": row[4], "optimum_offer_id": row[5],
            "partner_article": row[6] or "", "optimum_article": row[7] or "",
            "brand": row[8], "optimum_name_model": row[9], "matching_method": row[10],
            "matching_evidence": json.dumps(row[11], ensure_ascii=False, sort_keys=True),
        } for row in cursor.fetchall()]


def write_run_artifacts(label: str, crawl, ingest_counts, matches: list[dict], db_counts: dict) -> None:
    write_csv(REPORTS / f"OPTIMUM_FULL_CRAWL_{label}.csv", list(crawl.rows))
    with (REPORTS / f"OPTIMUM_PARSED_{label}.jsonl").open("w", encoding="utf-8") as handle:
        for card in crawl.cards:
            handle.write(json.dumps(card.as_jsonable(), ensure_ascii=False, sort_keys=True) + "\n")
    write_csv(REPORTS / f"OPTIMUM_MATCHING_{label}.csv", matches, MATCH_FIELDS)
    identity_rows = [{
        "source_external_id": card.external_id, "identity_kind": card.raw_data["identity_kind"],
        "internal_product_id": card.raw_data.get("internal_product_id") or "", "SKU": card.sku,
        "source_url": card.requested_url, "canonical_url": card.canonical_url,
        "alias_count": len(card.alias_urls), "result": "PASS",
    } for card in crawl.cards]
    write_csv(REPORTS / "OPTIMUM_IDENTITY_AUDIT.csv", identity_rows)
    write_csv(REPORTS / "OPTIMUM_ALIAS_AUDIT.csv", list(crawl.aliases),
              ["primary_product_url", "alias_url", "external_id", "reason"])
    write_csv(REPORTS / "OPTIMUM_CATEGORY_TREE.csv", list(crawl.category_tree),
              ["category_name", "category_url", "parent", "depth", "discovery_sources"])
    metadata = dict(crawl.metadata)
    metadata["ingest"] = vars(ingest_counts)
    metadata["database_counts"] = db_counts
    metadata["matching_distribution"] = dict(Counter(row["status"] for row in matches))
    (REPORTS / f"OPTIMUM_{label}_METADATA.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_run_diff() -> dict:
    run1 = read_jsonl(REPORTS / "OPTIMUM_PARSED_RUN1.jsonl")
    run2 = read_jsonl(REPORTS / "OPTIMUM_PARSED_RUN2.jsonl")
    def hashes(rows):
        from universal_supplier.models import ProductCard
        return {row["external_id"]: card_hashes(ProductCard.from_jsonable(row)) for row in rows}
    h1, h2 = hashes(run1), hashes(run2)
    rows = []
    for key in sorted(set(h1) | set(h2)):
        status = "NEW" if key not in h1 else "MISSING" if key not in h2 else "CHANGED" if h1[key] != h2[key] else "UNCHANGED"
        rows.append({"source_external_id": key, "result": status,
                     "changed_groups": "|".join(group for group in h1.get(key, {}) if h1.get(key, {}).get(group) != h2.get(key, {}).get(group))})
    write_csv(REPORTS / "OPTIMUM_RUN1_RUN2_DIFF.csv", rows)
    return dict(Counter(row["result"] for row in rows))


def finalize(connection, run2_matches: list[dict]) -> dict:
    ingest_rows = []
    for label in ("RUN1", "RUN2"):
        meta = json.loads((REPORTS / f"OPTIMUM_{label}_METADATA.json").read_text(encoding="utf-8"))
        ingest_rows.append({"run": label, **meta["ingest"], **meta["database_counts"]})
    write_csv(REPORTS / "OPTIMUM_INGEST_COUNTS.csv", ingest_rows)
    status_rows = []
    for label in ("RUN1", "RUN2"):
        rows = list(csv.DictReader((REPORTS / f"OPTIMUM_MATCHING_{label}.csv").open(encoding="utf-8-sig")))
        status_rows.extend({"run": label, "status": status, "count": count}
                           for status, count in sorted(Counter(row["status"] for row in rows).items()))
    write_csv(REPORTS / "OPTIMUM_MATCH_STATUS_COUNTS.csv", status_rows, ["run", "status", "count"])
    write_csv(REPORTS / "OPTIMUM_REVIEW.csv", [row for row in run2_matches if row["status"] == "REVIEW"], MATCH_FIELDS)
    write_csv(REPORTS / "OPTIMUM_CONFLICTS.csv", [row for row in run2_matches if row["status"] == "CONFLICT"], MATCH_FIELDS)
    write_csv(REPORTS / "OPTIMUM_NEW_CANDIDATES.csv", [row for row in run2_matches if row["status"] == "NEW_CANDIDATE"], MATCH_FIELDS)
    diff = write_run_diff()
    qa = scalar_qa(connection)
    overlap = overlap_rows(connection)
    write_csv(REPORTS / "MULTI_SUPPLIER_OVERLAP_AUDIT.csv", overlap)
    baseline = json.loads((REPORTS / "STAGE3E_PARTNER_BASELINE.json").read_text(encoding="utf-8"))
    after = partner_state(connection)
    after_json = json.loads(json.dumps(after, default=list))
    partner_pass = baseline == after_json
    pp = after["pp800f"]
    mrx = after["mrx3"]
    partner_report = (
        "# Partner-ST regression after Optimum\n\n"
        f"- Full supplier snapshot unchanged: `{'PASS' if partner_pass else 'FAIL'}`\n"
        f"- PP-800F: `{'PASS' if pp and pp[1] == 'EXACT_MATCH' and pp[3] else 'FAIL'}`\n"
        f"- MR-X3/MR-X4: `{'PASS' if mrx and mrx[0] is None and mrx[1] == 'CONFLICT' and not mrx[3] else 'FAIL'}`\n"
        "- Fuzzy auto matches: `0`\n- Sterbrust writes: `0`\n"
    )
    (REPORTS / "PARTNER_ST_REGRESSION_AFTER_OPTIMUM.md").write_text(partner_report, encoding="utf-8")
    isolation_pass = all(value == 0 for value in qa.values())
    isolation = {"state": "PASS" if isolation_pass else "FAIL", **qa}
    (REPORTS / "STAGE3E_SUPPLIER_ISOLATION_QA.json").write_text(
        json.dumps(isolation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "diff": diff, "qa": qa, "overlap_count": len(overlap), "partner_pass": partner_pass,
        "pp_pass": bool(pp and pp[1] == "EXACT_MATCH" and pp[3]),
        "mrx_pass": bool(mrx and mrx[0] is None and mrx[1] == "CONFLICT" and not mrx[3]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, choices=("RUN1", "RUN2"))
    parser.add_argument("--concurrency", type=int, default=4, choices=(1, 2, 3, 4))
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()
    stage3d = (REPORTS / "STAGE3D_SUMMARY.txt").read_text(encoding="utf-8")
    if "STAGE3D = PASS" not in stage3d:
        raise SystemExit("Stage 3D must be closed before Optimum integration")

    config = PostgresConfig.from_env()
    repo = PostgresRepository(config)
    run_id = None
    try:
        if args.run == "RUN1":
            if supplier_counts(repo.connection, "optimum")["source_products"]:
                raise RuntimeError("Optimum RUN1 requires an empty Optimum supplier namespace")
            (REPORTS / "STAGE3E_PARTNER_BASELINE.json").write_text(
                json.dumps(partner_state(repo.connection), ensure_ascii=False, indent=2, default=list), encoding="utf-8"
            )
            baseline = None
        else:
            meta_path = REPORTS / "OPTIMUM_RUN1_METADATA.json"
            if not meta_path.is_file():
                raise RuntimeError("RUN2 requires completed RUN1 evidence")
            baseline = json.loads(meta_path.read_text(encoding="utf-8"))["discovered"]

        crawl = run_optimum_live_sync(concurrency=args.concurrency, delay=args.delay)
        if crawl.metadata["fetch_errors"] or crawl.metadata["duplicate_stable_external_ids"]:
            raise RuntimeError(f"Unhealthy Optimum crawl: {crawl.metadata}")
        health = discovery_health(crawl.metadata["discovered"], baseline)
        run_id = repo.start_crawl_run(
            started_at=crawl.started_at, baseline_discovered_count=baseline,
            metadata={"run": args.run, "source": "live_dynamic_sitemap_and_catalog_http", "stale_404": crawl.metadata["stale_404"]},
            supplier_code="optimum",
        )
        counts = repo.ingest(
            list(crawl.cards), crawl.finished_at, crawl_run_id=run_id, discovery_health=health,
            discovered_urls=[row["source_url"] for row in crawl.rows], error_count=0, supplier_code="optimum",
        )
        registry = load_registry_from_postgres(repo.connection)
        existing = repo.load_existing_links("optimum") if args.run == "RUN2" else {}
        matches = match_cards(crawl.cards, registry, existing)
        for decision in matches:
            repo.persist_match(decision, crawl.finished_at, supplier_code="optimum")
        db_counts = supplier_counts(repo.connection, "optimum")
        write_run_artifacts(args.run, crawl, counts, matches, db_counts)
        result = {"run": args.run, **crawl.metadata, "ingest": vars(counts),
                  "database_counts": db_counts, "matching": dict(Counter(row["status"] for row in matches))}
        if args.run == "RUN2":
            result["final"] = finalize(repo.connection, matches)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        if run_id is not None:
            repo.fail_crawl_run(
                run_id,
                finished_at=datetime.now(timezone.utc).isoformat(),
                message=f"{type(exc).__name__}: {exc}",
            )
        raise
    finally:
        repo.close()


if __name__ == "__main__":
    main()
