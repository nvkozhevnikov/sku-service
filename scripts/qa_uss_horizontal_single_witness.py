"""Independent read-only witness: one fixed kit in a column-headed table."""
from dataclasses import asdict
from decimal import Decimal
from bs4 import BeautifulSoup
from scripts import qa_ltt_uss_final as q
from universal_supplier.ltt_uss_capture import _card_gate


def witness():
    e=next(e for e in q.r.load(q.PLAN)['rows'] if e['supplier']=='uss_stanko' and e['index']==53)
    path=q.o.ROOT/e['journal'];assert q.r.digest(path.read_bytes())==e['journal_sha256']
    row=q.r.load(path);m=row['result'];response=q.r.load(q.r.OUT/m['response_record'])
    raw=(q.r.OUT/response['raw_path']).read_bytes()
    body=(q.r.OUT/'EVIDENCE/uss_stanko'/(m['evidence_sha256']+'.html')).read_bytes()
    assert q.r.digest(raw)==m['response_sha256']==response['response_sha256']
    assert q.r.digest(body)==m['evidence_sha256'] and q.o.sanitise_for_charset(raw,m['charset'])==body
    d=BeautifulSoup(body.decode('utf8'),'html.parser');plain=lambda n:' '.join(n.get_text(' ',strip=True).split())
    headings=d.find_all('h1');assert len(headings)==1 and plain(headings[0])=='Набор фиксаторов'
    tables=d.select('table.data');assert len(tables)==1
    table=tables[0];rows=[[plain(c) for c in tr.find_all(['td','th'],recursive=False)] for tr in table.find_all('tr')]
    assert rows==[['','Номер арт.','Цена, руб. с НДС','Наличие',''],['Набор фиксаторов 4 шт.','510 0004','1 440','под заказ','Заказать']]
    assert len(d.select('a[name^="art"]'))==1 and d.select_one('a[name^="art"]')['name']=='art5100004'
    controls=d.select('a.bnt_to_cart,a.bnt_zak');assert len(controls)==1 and controls[0] in table.descendants
    assert "zak('43','1317', '')"==controls[0].get('onclick')
    canonical=d.select_one('link[rel="canonical"]')['href'];assert canonical==e['url']==m['final_url']
    assert 'Комплект поставки: набор из 4 фиксаторов.' in plain(d)
    product=q.r.parse('uss_stanko',body,canonical)
    status,diags=_card_gate('uss_stanko',raw.decode(m['charset'] or 'utf8','replace'),canonical)
    amount=Decimal(''.join(rows[1][2].split()));assert amount==1440
    return dict(supplier='uss_stanko',index=53,source_id='uss:derevo/prinadlejnosti/fix.htm',url=canonical,
        expected=dict(title=plain(headings[0]),line_name=rows[1][0],article=rows[1][1],model=None,price=str(amount),currency='RUB',
            availability='PreOrder',raw_price=rows[1][2],raw_availability=rows[1][3],unit='fixed kit of four;1440 per kit, no division',outcome='PERSISTED_ELIGIBLE_SINGLE_SOURCE_PRODUCT'),
        actual=dict(product=asdict(product),gate_status=status.value,gate_diagnostics=diags,effective_outcome=e['outcome']),
        journal=e['journal'],journal_sha256=e['journal_sha256'],response_sha256=m['response_sha256'],evidence_sha256=m['evidence_sha256'],
        original_observed_at=m['observed_at'],charset=m['charset'],evidence_path=str((q.r.OUT/'EVIDENCE/uss_stanko'/(m['evidence_sha256']+'.html')).relative_to(q.o.ROOT)),
        raw_path=response['raw_path'],source_table_html=str(table),source_table_rows=rows,source_heading_html=str(headings[0]),
        source_order_control_html=str(controls[0]),page_text=plain(d),single_orderable_row=True,
        distinct_articles=['510 0004'],variant_matrix=False,current_code_hashes=q.r.code_hashes())
