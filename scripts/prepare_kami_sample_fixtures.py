"""Derive small session-free own-card fixtures from pinned current captures."""
import hashlib
import json
from pathlib import Path
from bs4 import BeautifulSoup
from universal_supplier.adapters.kami import KamiAdapter
from universal_supplier.models import FetchRecord

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05/CONTROLLED_SAMPLE'
OUT = ROOT / 'tests/fixtures/kami'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    variant_source = SOURCE.parent / 'SPECTR_VARIANT_SAMPLE'
    ledger = [(SOURCE, r) for r in json.loads((SOURCE / 'HTTP_AUDIT.json').read_text(encoding='utf-8'))]
    if (variant_source / 'HTTP_AUDIT.json').exists():
        ledger += [(variant_source, r) for r in json.loads((variant_source / 'HTTP_AUDIT.json').read_text(encoding='utf-8'))
                   if '/g5_42y_standart_2/' in r['url'] or '/g5_42ys_protivoshpindel_2/' in r['url']]
    selected_names = {}
    collected = SOURCE.parent / 'PRODUCT_RECONCILIATION'
    if (collected / 'CHECKPOINT.json').exists():
        state = json.loads((collected / 'CHECKPOINT.json').read_bytes())
        selected_types = set()
        for entry in state['cards'].values():
            saved_card = json.loads((collected / entry['payload_file']).read_bytes())
            card = saved_card['card']
            kinds = []
            if card['raw_data']['price_state'] == 'price_on_request':
                kinds.append('observed_price_on_request')
            if card['availability_normalized'] == 'incoming':
                kinds.append('observed_incoming')
            for kind in kinds:
                if kind in selected_types:
                    continue
                rec = saved_card['capture']
                # The exact evidence file can come from a reused pinned audit.
                folder = Path(saved_card['evidence_ref']).parent
                selected_names[rec['url']] = kind + '.html'
                ledger.append((folder, rec)); selected_types.add(kind)
                break
    evidence = []
    for folder, record in ledger:
        if 'html' not in record.get('content_type', '') or '?' in record['url']:
            continue
        raw = (folder / record['capture']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record['sha256']
        soup = BeautifulSoup(raw.decode('cp1251'), 'html.parser')
        grid = soup.select_one('.productGrid[data-product-id]')
        if grid is None:
            raise ValueError('controlled product scope missing')
        # Section controls contain visible characteristic-role labels. Keep the
        # text but remove interactivity; otherwise spindle/turret roles are lost.
        for node in list(grid.select('.tcontrol button')):
            node.name = 'span'
            node.attrs = {}
        for node in list(grid.select('script, style, form, input, button')):
            node.decompose()
        for node in grid.find_all(True):
            for attr in list(node.attrs):
                if attr not in {'class', 'href', 'src', 'alt', 'title', 'rel', 'data-tab-item',
                                'data-product-id', 'data-sku', 'data-currency'}:
                    del node.attrs[attr]
        html = '<!doctype html><html><head><meta charset="utf-8"><link rel="canonical" href="' + record['url'] + '"></head><body>' + str(grid) + '</body></html>'
        name = (selected_names.get(record['url']) or
                ('spectr_variant_standard.html' if '/g5_42y_standart_2/' in record['url'] else
                'spectr_variant_counterspindle.html' if '/g5_42ys_protivoshpindel_2/' in record['url'] else
                'ironmac_single_sale.html' if 'ironmac' in record['url'] else 'spectr_group_from.html'))
        (OUT / name).write_text(html, encoding='utf-8')
        card = KamiAdapter().parse_product(FetchRecord(record['url'], record['url'], 200, (), 1, 0, html))
        evidence.append({'fixture': name, 'url': record['url'], 'capture_sha256': record['sha256'],
                         'observed_at': record['observed_at'], 'fixture_sha256': hashlib.sha256((OUT / name).read_bytes()).hexdigest(),
                         'sanitization': 'own productGrid only; scripts/forms/events/other data attributes excluded',
                         'card': card.as_jsonable()})
    (ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05/CONTROLLED_PARSED.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'manifest.json').write_text(json.dumps([{k:v for k,v in item.items() if k != 'card'} for item in evidence], ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{'fixture': e['fixture'], 'identity': e['card']['external_id'],
                       'card_kind': e['card']['raw_data']['card_kind'], 'brand': e['card']['brand'],
                       'model': e['card']['raw_data']['full_model'], 'price': e['card']['price'],
                       'properties': len(e['card']['properties']), 'variants': len(e['card']['raw_data']['variant_evidence'])}
                      for e in evidence], ensure_ascii=False))


if __name__ == '__main__':
    main()
