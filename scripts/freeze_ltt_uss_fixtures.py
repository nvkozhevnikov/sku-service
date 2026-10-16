"""Build minimized sanitized fixtures from the existing audit, with provenance."""
from pathlib import Path
import hashlib
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bs4 import BeautifulSoup
from universal_supplier.ltt_uss_capture import sanitise_for_charset

root = Path(__file__).resolve().parents[1]
audit = root / 'reports/LTT_USS_INTEGRATION_2026-10-13/AUDIT'
out = root / 'tests/fixtures/ltt_uss'
out.mkdir(parents=True, exist_ok=True)
ledger = json.loads((audit / 'SOURCE_AUDIT_SUMMARY.json').read_text('utf-8'))['ledger']
provenance = []
for entry in ledger:
    if not entry['label'].startswith('sample:') or entry['label'] == 'sample:home':
        continue
    filename = entry['source'] + '__' + entry['label'].split(':', 1)[1] + '.html'
    raw = (audit / 'RAW' / filename).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == entry['sha256']
    charset = 'cp1251' if entry['source'] == 'uss_stanko' else 'utf-8'
    dom = BeautifulSoup(sanitise_for_charset(raw, charset).decode('utf-8'), 'html.parser')
    for node in dom.select('script,style,iframe,header,footer'):
        node.decompose()
    if entry['source'] in {'ltt_msk', 'ltt_sib'}:
        nodes = [dom.select_one('#breadcrumbs'), dom.select_one('.product')]
    elif entry['source'] == 'ltt_yug':
        h1 = dom.find('h1')
        nodes = [h1.find_parent(class_='container')]
    else:
        nodes = [dom.find('h1'), *dom.select('table.data')]
    canonical = dom.select_one('link[rel="canonical"]')
    html = '<!doctype html><html><head><meta charset="' + charset + '">' + (str(canonical) if canonical else '') + '</head><body>'
    html += ''.join(str(node) for node in nodes if node is not None) + '</body></html>'
    clean = BeautifulSoup(html, 'html.parser')
    for node in clean.find_all(True):
        for attr in list(node.attrs):
            if attr.startswith('on') or attr in {'data-key', 'action'}:
                del node[attr]
        if node.name == 'input' and node.get('name') not in {'id', 'count'}:
            node.decompose()
    body = str(clean).encode(charset, 'xmlcharrefreplace')
    (out / filename).write_bytes(body)
    provenance.append(dict(source=entry['source'], filename=filename, source_url=entry['final_url'],
                           charset=charset, original_sha256=entry['sha256'], fixture_sha256=hashlib.sha256(body).hexdigest(),
                           evidence_class='PACKAGED-EVIDENCE', transform='minimized sanitized DOM; original audit retained locally'))
(out / 'PROVENANCE.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n', 'utf-8')
print(json.dumps({'fixtures': len(provenance), 'bytes': sum((out / p['filename']).stat().st_size for p in provenance)}))
