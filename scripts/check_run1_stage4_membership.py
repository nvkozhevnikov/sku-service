"""Read-only SQL membership check for the interrupted Intervesp RUN1 prefix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.reconstruct_run1_checkpoint import reconstruct


EXPECTED_IDENTITY = ("stage4_commercial_qa", "stage4_runtime", "17.11", False, 7689025282387590508)


def check(manifest_path: Path, report_path: Path) -> dict:
    checkpoint = reconstruct(manifest_path, [report_path])
    prefix = checkpoint["sources"]["intervesp"]["terminal_prefix"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected_rows = [row for row in report["rows"] if row.get("source") == "intervesp"
                     and row.get("status") == "PERSISTED"]
    if len(expected_rows) != prefix or any(not row.get("external_id") for row in expected_rows):
        raise RuntimeError("Intervesp report prefix is not a complete stable-ID ledger")
    external_ids = [str(row["external_id"]) for row in expected_rows]
    if len(set(external_ids)) != len(external_ids):
        raise RuntimeError("Intervesp RUN1 report repeats a supplier external ID")
    with psycopg.connect(host="127.0.0.1", port=55447, dbname="stage4_commercial_qa",
                         user="stage4_runtime", options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                           "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
            identity = tuple(cursor.fetchone())
            if identity != EXPECTED_IDENTITY:
                raise RuntimeError("Stage4 PostgreSQL identity changed")
            cursor.execute("""SELECT sp.external_id,sp.source_url,sp.external_id_is_stable,
                                  count(DISTINCT o.id),count(DISTINCT c.id)
                           FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                           LEFT JOIN offers o ON o.source_product_id=sp.id
                           LEFT JOIN supplier_http_captures c ON c.source_product_id=sp.id
                           WHERE s.code='intervesp' AND sp.external_id = ANY(%s)
                           GROUP BY sp.id""", (external_ids,))
            found = {str(external_id): (url, stable, offers, captures)
                     for external_id, url, stable, offers, captures in cursor.fetchall()}
            cursor.execute("""SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                           WHERE s.code='intervesp' AND sp.external_id='9219'""")
            interrupted_card_rows = cursor.fetchone()[0]
    mismatches = []
    for row in expected_rows:
        value = found.get(str(row["external_id"]))
        if (value is None or value[0] != row["url"] or not value[1]
                or value[2] != 1 or value[3] < 1):
            mismatches.append({"external_id": row["external_id"], "url": row["url"]})
    return {"evidence_level": "LIVE_READ_ONLY_SQL", "identity": {
                "database": identity[0], "role": identity[1], "version": identity[2],
                "in_recovery": identity[3], "system_identifier": identity[4]},
            "manifest_sha256": checkpoint["manifest_sha256"],
            "intervesp_completed_report_prefix": prefix,
            "intervesp_membership_confirmed": len(found) - len(mismatches),
            "mismatches": mismatches, "interrupted_external_id_9219_rows": interrupted_card_rows,
            "safe_resume_offset_if_target_is_stable": prefix if not mismatches and interrupted_card_rows == 0 else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = check(args.manifest, args.report)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    print(body)


if __name__ == "__main__":
    main()
