"""Manual full-sitemap run against an exact, isolated QA or local RC target.

No DB selector, Docker, scheduler, matching, selection, or production export.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.full_supplier_collection import (
    FullManifest, PublicPacer, discover_full_manifest, run_full_collection, verify_qa_identity,
)
from universal_supplier.postgres import PostgresConfig, PostgresRepository


def _validate_resume(manifest: FullManifest, source: str, offset: int, previous_report: Path | None) -> None:
    if offset < 0:
        raise ValueError("negative resume offset")
    if offset == 0:
        return
    if source == "all" or previous_report is None:
        raise ValueError("resume requires one source and a previous report")
    previous = json.loads(previous_report.read_text(encoding="utf-8"))
    if previous.get("manifest_sha256") != manifest.sha256:
        raise ValueError("resume manifest SHA-256 changed")
    candidates = [item for item in manifest.candidates if item.source == source]
    if offset > len(candidates):
        raise ValueError("resume offset exceeds source manifest")
    completed = {row.get("url"): row for row in previous.get("rows", [])
                 if row.get("source") == source and row.get("status") in {"PERSISTED", "REVIEW", "AMBIGUOUS"}}
    for candidate in candidates[:offset]:
        row = completed.get(candidate.url)
        if row is None or row.get("expected_model") != candidate.expected_model:
            raise ValueError("resume prefix is incomplete or changed")


def _preflight(repository: PostgresRepository) -> dict:
    identity = verify_qa_identity(repository)
    with repository.connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM suppliers WHERE code IN ('intervesp','beka_mak','beka_mak_tr') AND enabled")
        if cursor.fetchone()[0]:
            raise RuntimeError("passive supplier is enabled; QA write refused")
        cursor.execute("SELECT count(*) FROM offers WHERE active")
        if cursor.fetchone()[0]:
            raise RuntimeError("active offers found; QA write refused")
        cursor.execute("SELECT count(*) FROM source_products WHERE catalog_product_id IS NOT NULL")
        if cursor.fetchone()[0]:
            raise RuntimeError("canonical links found; QA write refused")
        cursor.execute("SELECT count(*) FROM product_matches")
        if cursor.fetchone()[0]:
            raise RuntimeError("product_matches found; QA write refused")
        counts = {}
        for table in ("source_products", "offers", "supplier_http_captures",
                      "offer_commercial_observations", "product_matches", "catalog_products"):
            cursor.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cursor.fetchone()[0]
    return {"identity": identity, "counts": counts}


def _preflight_rc(repository: PostgresRepository) -> dict:
    """The historical namespaces may have matches/active offers; commercial may not."""
    repository.gate()
    with repository.connection.cursor() as cursor:
        cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                       "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
        identity = tuple(cursor.fetchone())
        if identity != ("universal_supplier_server", "rc_runtime", "17.11", False,
                        7691270601420084116):
            raise RuntimeError("dedicated RC PostgreSQL identity changed")
        cursor.execute("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")
        if tuple(cursor.fetchone()) != (False, False, False):
            raise RuntimeError("RC runtime role has unexpected privileges")
        cursor.execute("SELECT code,enabled FROM suppliers WHERE code IN ('intervesp','beka_mak','beka_mak_tr')")
        states = dict(cursor.fetchall())
        if states != {"intervesp": False, "beka_mak": False, "beka_mak_tr": False}:
            raise RuntimeError("commercial supplier namespace or disabled state changed")
        cursor.execute("SELECT count(*) FROM offers o JOIN suppliers s ON s.id=o.supplier_id "
                       "WHERE s.code IN ('intervesp','beka_mak','beka_mak_tr') AND o.active")
        if cursor.fetchone()[0]:
            raise RuntimeError("commercial offer became active")
        counts = {}
        for table in ("source_products", "offers", "supplier_http_captures",
                      "offer_commercial_observations", "product_matches", "catalog_products"):
            cursor.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cursor.fetchone()[0]
    return {"identity": identity, "counts": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sitemap-backed QA collection of three independent sources")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", choices=("all", "intervesp", "beka_mak", "beka_mak_tr"), default="all")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--resume-report", type=Path)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--confirm-target", choices=("stage4_commercial_qa", "universal_supplier_server"))
    args = parser.parse_args()
    if args.write and not args.confirm_target:
        parser.error("write requires an exact --confirm-target")
    expected = FullManifest.from_jsonable(json.loads(args.manifest.read_text(encoding="utf-8")))
    pacer = PublicPacer()
    discovery_evidence: list[dict] = []
    current = discover_full_manifest(pacer=pacer, evidence_log=discovery_evidence)
    if current.sha256 != expected.sha256:
        raise RuntimeError("discovery manifest changed; no product capture was started")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "discovery_evidence.json").write_text(
        json.dumps({"manifest_sha256": current.sha256, "sitemaps": discovery_evidence},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    _validate_resume(current, args.source, args.offset, args.resume_report)
    sources = tuple(("intervesp", "beka_mak", "beka_mak_tr") if args.source == "all" else (args.source,))
    target = (PostgresConfig("127.0.0.1", 55449, "universal_supplier_server", "rc_runtime", None, "disable")
              if args.confirm_target == "universal_supplier_server" else
              PostgresConfig("127.0.0.1", 55447, "stage4_commercial_qa", "stage4_runtime", None, "disable"))
    repository = PostgresRepository(target) if args.write else None
    preflight = _preflight_rc if args.confirm_target == "universal_supplier_server" else _preflight
    try:
        before = preflight(repository) if repository else None
        result = run_full_collection(
            manifest=current, repository=repository, evidence_dir=args.report_dir / "evidence",
            report_path=args.report_dir / "report.json", dry_run=not args.write,
            sources=sources, limit=args.limit, offset=args.offset, pacer=pacer,
            write_identity_validator=_preflight_rc if args.confirm_target == "universal_supplier_server" else None,
            on_progress=lambda row, processed, total: print(json.dumps({
                "progress": f"{processed}/{total}", "source": row["source"],
                "status": row["status"], "http_status": row["http_status"],
                "model": row.get("model")}, ensure_ascii=False), flush=True),
        )
        after = preflight(repository) if repository and not result.get("aborted_reason") else None
        summary = {"manifest_sha256": current.sha256, "selected": result["selected_count"],
                   "attempted": len(result["rows"]), "blocked_sources": result["blocked_sources"],
                   "counts_before": before, "counts_after": after,
                   "aborted_reason": result.get("aborted_reason")}
        (args.report_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        if repository:
            repository.close()


if __name__ == "__main__":
    main()
