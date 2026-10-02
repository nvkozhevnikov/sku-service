"""Compare every PERSISTED RUN1 report row with the isolated RC database.

This uses only SELECTs and never treats REVIEW/AMBIGUOUS as confirmed products.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from universal_supplier.full_supplier_collection import FullManifest


EXPECTED_IDENTITY = ("universal_supplier_server", "rc_readonly", "17.11", False,
                     7691270601420084116)


def verify(manifest_path: Path, reports: list[Path]) -> dict:
    manifest = FullManifest.from_jsonable(json.loads(manifest_path.read_text(encoding="utf-8")))
    candidates = {(item.source, item.url): item.expected_model for item in manifest.candidates}
    ledger: dict[tuple[str, str], dict] = {}
    status_counts: dict[str, dict[str, int]] = {}
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("manifest_sha256") != manifest.sha256:
            raise RuntimeError(f"RUN1 manifest checksum changed: {path}")
        for row in report.get("rows", []):
            key = (row.get("source"), row.get("url"))
            if key not in candidates or row.get("expected_model") != candidates[key]:
                raise RuntimeError(f"RUN1 report has out-of-manifest card: {path}")
            terminal = row.get("status") in {"PERSISTED", "REVIEW", "AMBIGUOUS"}
            terminal = terminal or (row.get("status") == "HTTP_STATUS" and row.get("http_status") in {404, 410})
            if terminal:
                ledger[key] = row
    for source in ("intervesp", "beka_mak", "beka_mak_tr"):
        source_rows = [row for (code, _), row in ledger.items() if code == source]
        status_counts[source] = {status: sum(row["status"] == status for row in source_rows)
                                 for status in ("PERSISTED", "REVIEW", "AMBIGUOUS", "HTTP_STATUS")}
        if len(source_rows) != sum(item.source == source for item in manifest.candidates):
            raise RuntimeError(f"RUN1 {source} is not terminal for the pinned manifest")
    mismatches = []
    with psycopg.connect(host="127.0.0.1", port=55449, dbname="universal_supplier_server",
                         user="rc_readonly", options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                           "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
            if tuple(cursor.fetchone()) != EXPECTED_IDENTITY:
                raise RuntimeError("RC read-only database identity changed")
            for (source, url), row in sorted(ledger.items()):
                if row["status"] != "PERSISTED":
                    continue
                external_id = str(row.get("external_id") or "")
                if not external_id:
                    mismatches.append({"source": source, "url": url, "reason": "report_missing_external_id"})
                    continue
                cursor.execute("""SELECT sp.source_url,sp.external_id_is_stable,
                                      (SELECT count(*) FROM offers o WHERE o.source_product_id=sp.id),
                                      (SELECT count(*) FROM supplier_http_captures c WHERE c.source_product_id=sp.id)
                               FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                               WHERE s.code=%s AND sp.external_id=%s""", (source, external_id))
                rows = cursor.fetchall()
                if len(rows) != 1 or rows[0][0] != url or not rows[0][1] or rows[0][2] != 1 or rows[0][3] < 1:
                    mismatches.append({"source": source, "external_id": external_id, "url": url,
                                       "reason": "missing_or_nonunique_product_offer_capture"})
    return {"manifest_sha256": manifest.sha256, "database_identity": EXPECTED_IDENTITY,
            "terminal_statuses": status_counts, "persisted_membership_mismatches": mismatches,
            "verified": not mismatches}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.manifest, args.report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": result["verified"], "terminal_statuses": result["terminal_statuses"],
                      "mismatches": len(result["persisted_membership_mismatches"])}, ensure_ascii=False))
    if not result["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
