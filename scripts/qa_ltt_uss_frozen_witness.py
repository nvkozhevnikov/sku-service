"""Read-only FULL QA evidence witness. Does not repair parsing or persist data."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch
import sys
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import run_ltt_uss_run2_offline as offline
from universal_supplier.ltt_uss_capture import _card_gate

OUT = offline.r.BASE / 'FULL_QA_2026-10-15'


@contextmanager
def qa_guard():
    """No supplier HTTP and no persistence calls, even accidental ones."""
    def deny(*args, **kwargs):
        raise offline.OfflineViolation('QA_SOURCE_PERSISTENCE_PROHIBITED')
    with ExitStack() as stack:
        counters = stack.enter_context(offline.offline_guard())
        for name in ('persist_commercial_observation', '_persist_passive_observation'):
            stack.enter_context(patch.object(offline.r.PostgresRepository, name, deny))
        yield counters


def plain_text(node):
    return ' '.join(node.get_text(' ',strip=True).split())


def airstar_witness(*, baseline_seal, proposed_transition=None):
    """Independent DOM read of the exact saved row, without price/parser helpers."""
    run = offline.frozen_run(baseline_seal=baseline_seal, proposed_transition=proposed_transition)
    path = run.journal_path('uss_stanko',23)
    row = offline.r.load(path)
    meta = row['result']
    response = offline.r.load(offline.safe_path(offline.r.OUT,meta['response_record']))
    raw = offline.safe_path(offline.r.OUT,response['raw_path']).read_bytes()
    assert offline.r.digest(raw)==meta['response_sha256']==response['response_sha256']
    evidence_path=offline.r.OUT/'EVIDENCE/uss_stanko'/(meta['evidence_sha256']+'.html')
    body=evidence_path.read_bytes()
    assert offline.r.digest(body)==meta['evidence_sha256']
    assert offline.sanitise_for_charset(raw,meta['charset'])==body
    dom=BeautifulSoup(body.decode('utf8'),'html.parser')
    h1=dom.find_all('h1')
    assert len(h1)==1
    tables=[]
    for table in dom.find_all('table'):
        if table.find('table'):continue
        rows=[]
        for tr in table.find_all('tr'):
            cells=tr.find_all(['td','th'],recursive=False)
            if cells and not any(c.find('table') for c in cells):
                rows.append([plain_text(c) for c in cells])
        if any(v[:2]==['Номер арт.','202 8753'] for v in rows):
            tables.append((table,rows))
    assert len(tables)==1
    table,rows=tables[0]
    model=[v[1] for v in rows if len(v)==2 and v[0]=='Модель']
    prices=[v for v in rows if v and v[0].startswith('Цена')]
    stocks=[v[1] for v in rows if len(v)==2 and v[0]=='Наличие']
    articles=[v[1] for v in rows if len(v)==2 and v[0]=='Номер арт.']
    assert model==['Airstar 703/270/10 H'] and articles==['202 8753']
    assert len(prices)==1 and prices[0][1]=='189 360' and 'руб' in prices[0][0]
    assert stocks==['под заказ']
    assert [a['name'] for a in dom.select('a[name^="art"]')]==['art2028753']
    canonical=dom.select_one('link[rel="canonical"]')['href']
    assert canonical==row['url']==meta['final_url']
    status,diagnostics=_card_gate('uss_stanko',body.decode('utf8'),canonical)
    parsed=offline.r.parse('uss_stanko',body,canonical)
    return dict(supplier='uss_stanko',index=23,source_id='uss:compressor/porsche/airstar_7031.htm',
        url=canonical,title=plain_text(h1[0]),manufacturer_article=articles[0],model=model[0],
        expected_price='189360',expected_currency='RUB',expected_availability='PreOrder',
        raw_price=prices[0][1],raw_price_label=prices[0][0],raw_availability=stocks[0],
        table_attributes=table.attrs,independent_product_tables=1,order_anchor='art2028753',
        response_sha256=meta['response_sha256'],evidence_sha256=meta['evidence_sha256'],
        charset=meta['charset'],original_observed_at=meta['observed_at'],
        journal=str(path.relative_to(offline.ROOT)),journal_sha256=offline.r.digest(path.read_bytes()),
        evidence_path=str(evidence_path.relative_to(offline.ROOT)),raw_path=response['raw_path'],
        source_table_html=str(table),source_table_rows=rows,
        run1_outcome=row['outcome'],actual_gate_status=status.value,actual_gate_diagnostics=list(diagnostics),
        actual_parser_price_state=parsed.price.state.value,actual_parser_price=str(parsed.price.current_price),
        actual_parser_availability=parsed.availability,actual_parser_external_id=parsed.site_internal_id,
        code_hashes=offline.r.code_hashes())
