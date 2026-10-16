"""Bounded source-identity preflight before canary persistence; no DB writes."""
from dataclasses import asdict
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from universal_supplier.adapters.ltt_yug import parse_ltt_yug_detail
from universal_supplier.full_supplier_collection import PublicPacer
from universal_supplier.ltt_uss_capture import capture_source_html
from universal_supplier.http_capture import CaptureStatus
from universal_supplier.ltt_uss_identity import source_pair_relationship

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/LTT_USS_INTEGRATION_2026-10-13'


def main():
    manifest = json.loads((OUT / 'MANIFESTS/ltt_yug.json').read_text('utf-8'))
    twins = [r for r in manifest['candidates'] if r.get('similar_slug_url')]
    relationships = []
    pacer = PublicPacer(20)
    rows, failures = [], []
    for twin in twins:
        pair = []
        for url in (twin['similar_slug_url'], twin['canonical_url']):
            pacer.before_get()
            result = capture_source_html(url, supplier='ltt_yug', evidence_dir=OUT / 'TWIN_PREFLIGHT/EVIDENCE')
            product = parse_ltt_yug_detail(result.evidence_body.decode('utf-8'), source_url=url) if result.status == CaptureStatus.SUCCESS else None
            record = dict(supplier='ltt_yug', requested_url=url, final_url=result.final_url, http_status=result.http_status, status=result.status.value,
                          observed_at=result.observed_at.isoformat(), response_sha256=result.response_sha256, evidence_sha256=result.evidence_sha256,
                          evidence_ref=result.evidence_ref, source_id=product.site_internal_id if product else None,
                          title=product.name if product else None, price=str(product.price.current_price) if product and product.price.current_price else None,
                          price_state=product.price.state.value if product else None, availability=product.availability if product else None)
            pair.append(record)
            rows.append(record)
            print(json.dumps(record, ensure_ascii=True), flush=True)
            if result.status != CaptureStatus.SUCCESS:
                failures.append(dict(reason='TWIN_PREFLIGHT_CAPTURE_FAILED', url=url, status=result.status.value))
                break
        if not failures and len(pair) == 2:
            relationships.append(dict(relationship=source_pair_relationship(*pair), urls=[r['requested_url'] for r in pair]))
        receipt = dict(status='BLOCKED' if failures else 'IN_PROGRESS', http_gets=len(rows), db_writes=0, rows=rows, failures=failures, relationships=relationships)
        (OUT / 'TWIN_PREFLIGHT').mkdir(exist_ok=True)
        (OUT / 'TWIN_PREFLIGHT/CURRENT_POLICY_RESULT.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', 'utf-8')
        if failures:
            print('LTT_YUG_TWIN_POLICY_BLOCKED', flush=True)
            return 1
    receipt['status'] = 'SOURCE_RELATIONSHIPS_RECORDED'
    (OUT / 'TWIN_PREFLIGHT/CURRENT_POLICY_RESULT.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
