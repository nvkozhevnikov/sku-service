"""Run exactly four read-only public-detail HTTP captures; no database access."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.adapters.bekamak import parse_bekamak_detail
from universal_supplier.adapters.intervesp import parse_intervesp_detail
from universal_supplier.http_capture import CaptureStatus, capture_public_html


OUT = ROOT / "reports" / "INTERVESP_BEKAMAK_HTTP_PILOT_2026-09-24"
EVIDENCE = OUT / "sanitized_evidence"
TARGETS = (
    ("intervesp", "8992", "BMS-230DG", "https://intervesp.ru/catalog/lentochnopilnye-ruchnye-metall/gorizontalnyy-otreznoy-lentochnopilnyy-ruchnoy-beka-mak-bms-230dg/", parse_intervesp_detail),
    ("beka_mak", "19240", "BMS-230DG", "https://beka-mak.su/product/ruchnoy_lentochnopilnyy_stanok_beka_mak_bms_230dg/", parse_bekamak_detail),
    ("intervesp", "9028", "BMSY-440DGH-WP2", "https://intervesp.ru/catalog/konsolnye-poluavtomaticheskie-lentochnopilnye-stanki/gorizontalnyy-otreznoy-lentochnopilnyy-poluavtomaticheskiy-beka-mak-bmsy-440dgh-wp2/", parse_intervesp_detail),
    ("beka_mak", "19224", "BMSY-440DGH", "https://beka-mak.su/product/poluavtomaticheskiy_lentochnopilnyy_stanok_beka_mak_bmsy_440dgh/", parse_bekamak_detail),
)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for site, external_id, expected_model, url, parser in TARGETS:
        result = capture_public_html(url, evidence_dir=EVIDENCE, timeout_seconds=15, max_attempts=2, expected_model=expected_model)
        row = {
            "site": site,
            "expected_external_id": external_id,
            "expected_model": expected_model,
            "requested_url": url,
            "capture_status": result.status.value,
            "final_url": result.final_url,
            "http_status": result.http_status,
            "content_type": result.content_type,
            "observed_at": result.observed_at.isoformat(),
            "redirects": result.redirects,
            "response_sha256": result.response_sha256,
            "evidence_sha256": result.evidence_sha256,
            "evidence_ref": result.evidence_ref,
            "diagnostics": result.diagnostics,
        }
        if result.status is CaptureStatus.SUCCESS and result.capture is not None:
            source = (EVIDENCE / (result.final_url.split("//", 1)[1].split("/", 1)[0]) / f"{result.evidence_sha256}.html").read_text(encoding="utf-8")
            product = parser(source, source_url=result.final_url)
            row.update({
                "parsed_internal_id": product.site_internal_id,
                "supplier_model": product.supplier_model,
                "manufacturer_article": product.manufacturer_article,
                "price_state": product.price.state.value,
                "price": None if product.price.current_price is None else str(product.price.current_price),
                "old_price": None if product.price.old_price is None else str(product.price.old_price),
                "currency": product.price.currency,
                "availability": product.availability,
                "parser_diagnostics": product.diagnostics + product.price.diagnostics,
            })
        rows.append(row)
    (OUT / "http_pilot_report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
