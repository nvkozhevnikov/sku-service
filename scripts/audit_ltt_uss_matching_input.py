"""Offline loader impact only. Never calls match_cards/match_product or a DB."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from universal_supplier.identity_service import _load_cards, source_product
from universal_supplier.matching_service import _source
from scripts.run_ltt_uss_matching import EXPECTED_SCOPE


def load(p):
    return json.loads(p.read_text(encoding='utf-8'))


def sha(p):
    return hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()


def audit(checkpoint: Path, output: Path):
    pins = load(checkpoint / 'FINAL_ARTIFACT_SEAL.json')
    snapshot = checkpoint / 'SOURCE_LIVE_SNAPSHOT.json'
    assert sha(snapshot) == pins[snapshot.name]
    accepted = checkpoint.parent / 'PAGE_ROLE_REMEDIATION_2026-10-16'
    accepted_pins = load(checkpoint / 'ACCEPTED_EVIDENCE_PINS.json')
    supplier_ids = {}
    for code in EXPECTED_SCOPE:
        packet = sorted((accepted / 'FRESH_FULL_QA/PERSISTED_ROWS' / code).glob('*.json'))[0]
        assert sha(packet) == accepted_pins[str(packet.relative_to(accepted))]
        supplier_ids[load(packet)['product']['supplier_id']] = code
    frozen = load(snapshot)
    expected = Counter()
    rows = []
    evidence = []
    for p, o in frozen['product_offer_projections']:
        if not p['active']:
            continue
        assert p['external_id_is_stable'] and p['external_id']
        code = supplier_ids[p['supplier_id']]
        expected[code] += 1
        def number(value):
            return None if value is None else Decimal(str(value))
        rows.append((p['id'], code, p['external_id'], p['sku'] or '', p['name'], p['source_url'],
            p['canonical_url'] or p['source_url'], p['brand_raw'] or '', p['manufacturer_raw'] or '',
            p['description_text'] or '', p['description_html'] or '', p['raw_data'], p['last_http_status'] or 200,
            number(o['price']), number(o['old_price']), o['currency'] or '', o['availability_raw'] or '',
            o['availability_normalized'] or 'unknown', number(o['quantity'])))
    assert dict(expected) == EXPECTED_SCOPE

    class FrozenConnection:
        def cursor(self): return self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=()):
            assert sql.lstrip().startswith('SELECT') and 'FOR UPDATE' not in sql
            if 'FROM source_products sp' in sql:
                assert 'sp.active' in sql and 's.code=%s' in sql
                self.rows = [r for r in rows if r[1] == params[0]]
            else:
                assert 'FROM product_properties' in sql or 'FROM product_category_paths' in sql
                self.rows = []  # This audit exercises missing relational-detail fallback.
        def fetchall(self): return self.rows

    def forbidden(*args, **kwargs):
        raise AssertionError('DB/NETWORK/MATCHING execution forbidden in loader audit')
    with patch('psycopg.connect', forbidden), patch('socket.socket.connect', forbidden), \
         patch('universal_supplier.identity_service.match_cards', forbidden), \
         patch('sterbrust_matching.matching.match_product', forbidden):
        for code in EXPECTED_SCOPE:
            cards, ids = _load_cards(FrozenConnection(), code)
            assert len(cards) == EXPECTED_SCOPE[code]
            for card in cards:
                product = _source(card)
                assert product == source_product(card.as_jsonable())
                assert product.supplier_code == code and product.key == card.external_id
                assert (code, card.external_id) in ids
                stored = card.raw_data['enrichment_evidence']
                assert product.product_kind == stored['product_kind']['product_kind']
                if stored.get('source_category'):
                    assert product.category == stored['source_category']
                if stored.get('source_brand'):
                    assert product.brand == stored['source_brand']
                evidence.append(dict(source=code,source_product_id=ids[(code,card.external_id)],
                    external_id=product.key,name=product.name,brand=product.brand,model=product.model,
                    stored_model=card.raw_data.get('source_identity',{}).get('raw_model') or stored.get('model_raw') or '',
                    product_kind=product.product_kind,category=product.category))
    assert len(evidence) == len({(p['source'],p['external_id']) for p in evidence}) == 2449
    assert not {225,226,227,228} & {p['source_product_id'] for p in evidence}
    assert sha(snapshot) == pins[snapshot.name]
    output.mkdir(parents=True,exist_ok=True)
    result = dict(status='PASS',active_scope=dict(expected),total=2449,article_rows_excluded=4,
        snapshot=str(snapshot.resolve()),snapshot_sha256=sha(snapshot),frozen_evidence='PACKAGED-EVIDENCE',
        verification='REPOSITORY-VERIFIED actual _load_cards via frozen SELECT cursor and shared matching conversion',
        registry_counts={t:frozen['fingerprints'][t]['count'] for t in
            ('sterbrust_products','sterbrust_product_supplier_identifiers','catalog_products')},
        db_connections=0,db_writes=0,supplier_http=0,matching_executed=False,
        relational_detail_fallback_exercised=True,
        stored_model_rows=sum(bool(r['stored_model']) for r in evidence),
        own_model_rows=sum(bool(r['model']) for r in evidence))
    (output/'LOADER_FULL_SCOPE_ROWS.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
    (output/'LOADER_FIX.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-checkpoint',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    audit(args.source_checkpoint,args.output)
