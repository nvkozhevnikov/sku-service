"""Independent read-only witness of revised-QA row368; no parser correction."""
from bs4 import BeautifulSoup
from scripts import qa_ltt_uss_revised as q


def witness():
    entry=next(e for e in q.r.load(q.f.PHASE/'EFFECTIVE_ROW_PLAN.json')['rows'] if e['supplier']=='uss_stanko' and e['index']==368)
    path=q.o.ROOT/entry['journal'];assert q.r.digest(path.read_bytes())==entry['journal_sha256']
    row=q.r.load(path);m=row['result']
    response=q.r.load(q.r.OUT/m['response_record']);raw=(q.r.OUT/response['raw_path']).read_bytes()
    body=(q.r.OUT/'EVIDENCE/uss_stanko'/(m['evidence_sha256']+'.html')).read_bytes()
    assert q.r.digest(raw)==m['response_sha256']==response['response_sha256']
    assert q.r.digest(body)==m['evidence_sha256'] and q.o.sanitise_for_charset(raw,m['charset'])==body
    dom=BeautifulSoup(body.decode('utf8'),'html.parser')
    headings=dom.select('h2.zagolovok_u');assert len(headings)==1 and not dom.find('h1')
    title=' '.join(headings[0].get_text(' ',strip=True).split());assert title=='Бутылочный домкрат HSWH 5.'
    tables=dom.select('table.data');assert len(tables)==1
    rows=[]
    for tr in tables[0].find_all('tr'):
        cells=tr.find_all(['td','th'],recursive=False)
        if cells:rows.append([' '.join(c.get_text(' ',strip=True).split()) for c in cells])
    for expected in (['Модель','HSWH 5'],['Номер арт.','620 1001'],['Цена (с учетом НДС), руб.','2 580'],['Наличие','на складе']):assert expected in rows
    assert [a['name'] for a in dom.select('a[name^="art"]')]==['art6201001']
    assert len(tables[0].select('a.bnt_to_cart'))==1
    canonical=dom.select_one('link[rel="canonical"]')['href'];assert canonical==entry['url']==m['final_url']
    p=q.r.parse('uss_stanko',body,canonical);status,diag=q.f._card_gate('uss_stanko',body.decode('utf8'),canonical)
    assert p.site_internal_id=='uss:unikraft/domkrat/hswh.htm' and p.price.current_price==2580 and p.price.currency=='RUB' and p.availability=='InStock'
    return dict(supplier='uss_stanko',index=368,source_id=p.site_internal_id,url=canonical,source_title=title,model='HSWH 5',article='620 1001',
        expected_price=2580,expected_currency='RUB',expected_availability='InStock',raw_price='2 580',raw_availability='на складе',
        source_heading_tag='h2',source_heading_class='zagolovok_u',h1_count=0,single_own_commercial_table_count=1,
        actual_title=p.name,actual_price=str(p.price.current_price),actual_currency=p.price.currency,actual_availability=p.availability,
        actual_gate_status=status.value,actual_gate_diagnostics=list(diag),actual_parser_diagnostics=list(p.diagnostics),
        effective_outcome=entry['outcome'],journal=entry['journal'],journal_sha256=entry['journal_sha256'],
        evidence_sha256=m['evidence_sha256'],response_sha256=m['response_sha256'],original_time=m['observed_at'],
        evidence_path=str((q.r.OUT/'EVIDENCE/uss_stanko'/(m['evidence_sha256']+'.html')).relative_to(q.o.ROOT)),
        source_table_html=str(tables[0]),source_table_rows=rows,current_code_hashes=q.r.code_hashes())
