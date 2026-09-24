"""Render the Stage 5C commercial report through the existing full XML exporter.

This is a local diagnostic artifact only.  It does not read a database, run
selection, create a Sterbrust identity, or produce an ESOL payload.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.xml_export import supplier_xml_bytes


def rows_from_payloads(payloads: list[dict]) -> list[dict]:
    """Keep only persisted source observations, deduplicated by supplier/ID."""
    rows: dict[tuple[str, str], dict] = {}
    for payload in payloads:
        for item in payload.get("rows", []):
            if item.get("result") != "PERSISTED":
                continue
            external_id = str(item.get("external_id") or "").strip()
            site = str(item.get("site") or "").strip()
            if not site or not external_id:
                raise ValueError("persisted report row has no site/external ID")
            model = item.get("parsed_model") or item.get("expected_model") or ""
            rows[(site, external_id)] = {
                # Stage 5C reports deliberately do not export database primary
                # keys.  Leave this blank instead of inventing one.
                "source_product_id": "",
                "external_id": external_id,
                "supplier_code": site,
                "source_url": item.get("requested_url"),
                "name": model,
                "own_model": model,
                "price": item.get("price"),
                "currency": item.get("currency"),
                "price_source": item.get("price_state"),
                "availability_normalized": item.get("availability") or "unknown",
                "description_text": "",
                "category": "",
                "evidence_ref": item.get("evidence_ref"),
            }
    return [rows[key] for key in sorted(rows)]


def rows_from_reports(paths: list[Path]) -> list[dict]:
    return rows_from_payloads([json.loads(path.read_text(encoding="utf-8")) for path in paths])


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a read-only Stage 5C commercial QA XML.")
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = rows_from_reports(args.report)
    document = supplier_xml_bytes(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(document)
    print(json.dumps({"products": len(rows), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
