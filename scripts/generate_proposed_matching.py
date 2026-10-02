"""Read-only commercial matching proposals against a pinned GET-only registry."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.full_supplier_collection import FullManifest
from universal_supplier.matching_service import load_registry_from_csv
from universal_supplier.proposed_matching import classify_persisted_sources, proposal_summary
from universal_supplier.postgres import PostgresConfig
from sterbrust_matching.normalization import normalize_model


EXPECTED_REGISTRY_SHA256 = "aac0d70fea301468972bb652af7d264c690f25e2d5d0b72862925d19ae1402d0"
SOURCE_SQL = """
SELECT sp.id AS source_product_id, o.id AS offer_id, s.code AS supplier_code,
       sp.external_id, sp.name, sp.sku, sp.source_url, sp.canonical_url,
       sp.raw_data, sp.description_text, sp.last_http_status,
       o.availability_raw, o.availability_normalized,
       co.price_state, co.price, co.currency, co.observed_at
FROM source_products sp
JOIN suppliers s ON s.id=sp.supplier_id
JOIN offers o ON o.supplier_id=s.id AND o.source_product_id=sp.id AND o.offer_kind='default'
LEFT JOIN LATERAL (
    SELECT co.* FROM offer_commercial_observations co
    WHERE co.supplier_id=s.id AND co.source_product_id=sp.id
      AND COALESCE(co.region_code,'')=COALESCE(sp.raw_data->>'_commercial_projection_region','')
    ORDER BY co.observed_at DESC,co.id DESC LIMIT 1
) co ON true
WHERE s.code IN ('intervesp','beka_mak','beka_mak_tr')
ORDER BY s.code,sp.external_id
"""


def read_qa_rows(config=None, *, expected_system_identifier: int | None = None) -> tuple[dict, list[dict]]:
    import psycopg

    if config is None:
        kwargs = {"host": "127.0.0.1", "port": 55447, "dbname": "stage4_commercial_qa",
                  "user": "stage4_runtime", "sslmode": "disable"}
        expected = ("stage4_commercial_qa", "stage4_runtime", "17.11", False,
                    7689025282387590508)
    else:
        if expected_system_identifier is None:
            raise ValueError("server read requires pinned system identifier")
        kwargs = config.kwargs()
        expected = (config.dbname, config.user, "17.11", False, expected_system_identifier)
    with psycopg.connect(**kwargs, autocommit=True,
                         options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                           "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
            identity = cursor.fetchone()
            if identity != expected:
                raise RuntimeError("supplier database identity changed")
            cursor.execute(SOURCE_SQL)
            columns = [column.name for column in cursor.description]
            rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    return {"database": identity[0], "role": identity[1], "version": identity[2],
            "system_identifier": identity[4]}, rows


def prior_unresolved_urls(paths: list[Path], *, manifest_sha256: str | None = None) -> set[tuple[str, str]]:
    """A later stable parse cannot silently approve an earlier REVIEW identity."""
    unresolved: set[tuple[str, str]] = set()
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        if manifest_sha256 is not None and report.get("manifest_sha256") != manifest_sha256:
            raise RuntimeError(f"prior REVIEW report manifest changed: {path}")
        for row in report.get("rows", []):
            key = (str(row.get("source") or ""), str(row.get("url") or ""))
            if all(key) and row.get("status") in {"REVIEW", "AMBIGUOUS"}:
                unresolved.add(key)
    return unresolved


def classify_with_manifest(records: list[dict], manifest: FullManifest, registry,
                           *, prior_review_urls: set[tuple[str, str]] | None = None) -> tuple[list[dict], int]:
    """Quarantine persisted QA pilots not covered by the pinned full manifest."""
    expected_by_url = {(item.source, item.url): item.expected_model for item in manifest.candidates}
    in_manifest = []
    quarantined = []
    out_of_manifest = 0
    for record in records:
        key = (record["supplier_code"], record["source_url"])
        expected = expected_by_url.get(key)
        if expected is not None and key not in (prior_review_urls or set()):
            record["expected_model"] = expected
            in_manifest.append(record)
            continue
        if expected is None:
            out_of_manifest += 1
        # Historical QA pilots can be real captures without belonging to the
        # currently pinned full-run manifest. Preserve them as REVIEW, never
        # silently promote them to EXISTING or NEW.
        raw = record.get("raw_data") or {}
        model = str((raw.get("source_identity") or {}).get("raw_model") or record.get("sku") or "")
        quarantined.append({"source": record["supplier_code"], "external_id": record["external_id"],
                            "source_product_id": record["source_product_id"], "offer_id": record["offer_id"],
                            "source_url": record["source_url"], "name": record["name"],
                            "model": model, "execution": model, "model_key": normalize_model(model),
                            "new_candidate_id": "", "classification": "REVIEW", "sterbrust_product_id": "",
                            "proposed_sterbrust_id": "", "match_status": ("OUTSIDE_PINNED_MANIFEST" if expected is None
                                                                       else "PRIOR_REVIEW_PENDING_OPERATOR"),
                            "match_method": "none", "full_model_confirmed": False, "target_model_keys": [],
                            "match_conflicts": (["source_url_not_in_pinned_full_manifest"] if expected is None
                                                else ["prior_review_requires_manual_identity_confirmation"]),
                            "match_warnings": [], "price_state": record.get("price_state") or "",
                            "price": str(record["price"]) if record.get("price") is not None else None,
                            "currency": record.get("currency"),
                            "availability": record.get("availability_normalized") or "unknown",
                            "observed_at": str(record.get("observed_at") or "")})
    rows = classify_persisted_sources(in_manifest, registry) + quarantined
    rows.sort(key=lambda item: (item["source"], str(item["external_id"])))
    return rows, out_of_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", choices=("stage4_qa", "local_rc"), default="stage4_qa")
    parser.add_argument("--prior-review-report", type=Path, action="append", default=[])
    args = parser.parse_args()
    manifest = FullManifest.from_jsonable(json.loads(args.manifest.read_text(encoding="utf-8")))
    registry_sha = hashlib.sha256(args.registry.read_bytes()).hexdigest()
    if registry_sha != EXPECTED_REGISTRY_SHA256:
        raise RuntimeError("GET-only Sterbrust registry changed; matching stopped")
    registry = load_registry_from_csv(args.registry)
    if len(registry.products) != 57478:
        raise RuntimeError("active registry count changed")
    if args.target == "local_rc":
        config = PostgresConfig("127.0.0.1", 55449, "universal_supplier_server",
                                "rc_readonly", None, "disable")
        identity, records = read_qa_rows(config, expected_system_identifier=7691270601420084116)
    else:
        identity, records = read_qa_rows()
    rows, quarantined_count = classify_with_manifest(
        records, manifest, registry, prior_review_urls=prior_unresolved_urls(
            args.prior_review_report, manifest_sha256=manifest.sha256))
    body = {"generated_at": datetime.now(timezone.utc).isoformat(), "diagnostic_only": True,
            "qa_identity": identity, "manifest_sha256": manifest.sha256,
            "registry_sha256": registry_sha, "registry_active_products": len(registry.products),
            "summary": proposal_summary(rows), "out_of_manifest_review": quarantined_count, "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "rows": len(rows),
                      "summary": body["summary"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
