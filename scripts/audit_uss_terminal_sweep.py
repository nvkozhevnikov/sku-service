"""Offline semantic evidence inventory only; never repairs, persists or runs RUN2."""
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bs4 import BeautifulSoup
from scripts import qa_ltt_uss_final as q
r,o=q.r,q.o
OUT=r.BASE/'USS_TERMINAL_SWEEP_2026-10-15'


def plain(n):return ' '.join(n.get_text(' ',strip=True).split()) if n is not None else ''


def controls(n):
    return [dict(tag=a.name,classes=a.get('class',[]),text=plain(a),onclick=a.get('onclick'),html=str(a))
            for a in n.select('[onclick]') if re.search(r'\b(?:add_to_cart|zak)\s*\(',a.get('onclick',''))]


def table_facts(table,number):
    rows=[];column_headers=[]
    for tr in table.find_all('tr'):
        cells=tr.find_all(['td','th'],recursive=False)
        if not cells or any(c.find('table') for c in cells):continue
        vals=[plain(c) for c in cells]
        row=dict(values=vals,article_anchors=[a.get('name') for a in tr.select('a[name^="art"]')],controls=controls(tr),
                 colspans=[c.get('colspan','1') for c in cells])
        rows.append(row)
        groups={k:[i for i,v in enumerate(vals) if re.search(pattern,v,re.I)] for k,pattern in
            [('article',r'номер\s*арт|артикул'),('price',r'^цена'),('availability',r'^наличие')]}
        if all(groups.values()) and len({groups[k][0] for k in groups})==3:
            column_headers.append(dict(row_index=len(rows)-1,columns={k:groups[k][0] for k in groups}))
    columns=[]
    for header in column_headers:
        items=[]
        for ri,row in enumerate(rows[header['row_index']+1:],header['row_index']+1):
            if not row['controls'] and not row['article_anchors']:continue
            vals=row['values'];mapping=header['columns']
            if len(vals)<=max(mapping.values()):
                items.append(dict(row_index=ri,unresolved_column_span=True,raw_row=row));continue
            items.append(dict(row_index=ri,name=vals[0],article_raw=vals[mapping['article']],price_raw=vals[mapping['price']],
                availability_raw=vals[mapping['availability']],controls=row['controls'],article_anchors=row['article_anchors'],raw_row=row))
        columns.append(dict(**header,sellable_rows=items,sellable_row_count=len(items)))
    preceding=[]
    for a in table.find_all_previous(['h1','h2','div']):
        if a.name in {'h1','h2'} or any(str(c).startswith('zag') for c in a.get('class',[])):
            preceding.append(dict(tag=a.name,classes=a.get('class',[]),text=plain(a)[:500]))
            if len(preceding)==3:break
    values=[row['values'] for row in rows]
    relevant=bool(column_headers or controls(table) or table.select('a[name^="art"]') or 'data' in table.get('class',[]))
    return dict(table_index=number,classes=table.get('class',[]),parent_id=table.parent.get('id'),relevant=relevant,
        preceding_headings=preceding,rows=rows,column_header_tables=columns,html=str(table),controls=controls(table),
        article_anchors=[a.get('name') for a in table.select('a[name^="art"]')],
        model_rows=[v for v in values if v and v[0].lower()=='модель'],
        article_rows=[v for v in values if v and re.search(r'^номер.*арт|^артикул',v[0],re.I)],
        price_rows=[v for v in values if v and v[0].lower().startswith('цена')],
        stock_rows=[v for v in values if v and v[0].lower()=='наличие'])


def prepare():
    assert r.load(OUT/'PREFLIGHT.json')['status']=='PASS'
    assert not (OUT/'ALL_407_COLUMN_HEADER_SCAN.json').exists()
    plan=[e for e in r.load(q.PLAN)['rows'] if e['supplier']=='uss_stanko'];ledger=[];scans=[]
    snapshot=r.load(q.OUT/'AFTER_QA.json')
    uss=[p for p,offer in snapshot['product_offer_projections'] if (p['external_id'] or '').startswith('uss:')]
    assert len(uss)==219 and len({p['supplier_id'] for p in uss})==1
    frozen_ids={p['external_id']:p['id'] for p in uss}
    with q.q.guard() as network:
      for e in plan:
        path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256']
        row=r.load(path);m=row['result'];response=r.load(r.OUT/m['response_record']);raw=(r.OUT/response['raw_path']).read_bytes()
        body=(r.OUT/'EVIDENCE/uss_stanko'/(m['evidence_sha256']+'.html')).read_bytes()
        assert r.digest(raw)==m['response_sha256']==response['response_sha256']
        assert r.digest(body)==m['evidence_sha256'] and o.sanitise_for_charset(raw,m['charset'])==body
        dom=BeautifulSoup(body.decode('utf8'),'html.parser')
        tables=[table_facts(t,n) for n,t in enumerate(dom.find_all('table')) if not t.find('table')]
        table_groups=[t for t in tables if t['column_header_tables']]
        scan=dict(index=e['index'],url=e['url'],effective_outcome=e['outcome'],evidence_sha256=m['evidence_sha256'],response_sha256=m['response_sha256'],
            column_tables=table_groups,single_sellable_row_tables=[t['table_index'] for t in table_groups if any(c['sellable_row_count']==1 for c in t['column_header_tables'])])
        scans.append(scan)
        if e['outcome']!='PERSISTED':
            product=r.parse('uss_stanko',body,m['final_url'])
            status,diag=q.accepted.f._card_gate('uss_stanko',raw.decode(m['charset'] or 'utf8','replace'),m['final_url'])
            outcome='PERSISTED' if status.value=='SUCCESS' else ('MULTI_VARIANT' if 'MULTI_VARIANT' in diag else status.value)
            assert outcome==e['outcome']
            canonical=product.source_url;path_id=urlparse(canonical).path.lstrip('/')
            if path_id.endswith('index.htm'):path_id=path_id[:-len('index.htm')]
            page_id='uss:'+path_id.rstrip('/')
            item=dict(entry=e,index=e['index'],source_id=page_id,source_id_role='STATIC_CANONICAL_PAGE_IDENTITY_NOT_VARIANT_PRODUCT_ID',
                current_parser_site_internal_id=product.site_internal_id,canonical_url=canonical,self_canonical=canonical==e['url']==m['final_url'],
                journal_sha256=e['journal_sha256'],raw_response_sha256=m['response_sha256'],evidence_sha256=m['evidence_sha256'],charset=m['charset'],
                original_observed_at=m['observed_at'],evidence_path=str((r.OUT/'EVIDENCE/uss_stanko'/(m['evidence_sha256']+'.html')).relative_to(o.ROOT)),
                document_title=plain(dom.title),title_evidence=[dict(tag=a.name,classes=a.get('class',[]),text=plain(a),html=str(a)) for a in dom.select('h1,h2')],
                body_text=plain(dom.select_one('#div_print') or dom.select_one('#div_body') or dom.body or dom),
                full_page_controls=controls(dom),full_page_article_anchors=[a.get('name') for a in dom.select('a[name^="art"]')],
                tables=[t for t in tables if t['relevant']],current_parser_product=asdict(product),current_parser_reason=list(diag),
                frozen_db_presence=dict(evidence_class='PACKAGED-EVIDENCE',snapshot=str((q.OUT/'AFTER_QA.json').relative_to(o.ROOT)),
                    product_present=page_id in frozen_ids,source_product_id=frozen_ids.get(page_id)),semantic_decision=None)
            dest=OUT/'REVIEW_FACTS'/f"{e['index']:06d}.json";r.save(dest,item)
            ledger.append(dict(index=e['index'],outcome=e['outcome'],source_id=page_id,title=[a['text'] for a in item['title_evidence']],
                article_count=len(set(item['full_page_article_anchors'])),control_count=len(item['full_page_controls']),
                models=[v for t in item['tables'] for v in t['model_rows']],articles=[v for t in item['tables'] for v in t['article_rows']],
                prices=[v for t in item['tables'] for v in t['price_rows']],stocks=[v for t in item['tables'] for v in t['stock_rows']],
                column_rows=[c for t in item['tables'] for c in t['column_header_tables']],facts=str(dest.relative_to(OUT))))
        if len(scans)%50==0:print(f'FROZEN USS TABLE SHAPE AUDIT {len(scans)}/407',flush=True)
      assert len(scans)==407 and len(ledger)==188
      r.save(OUT/'ALL_407_COLUMN_HEADER_SCAN.json',dict(status='ALL_407_SOURCE_PAGES_EXAMINED',rows=scans,network=network))
      r.save(OUT/'REVIEW_LEDGER.json',ledger)
      r.save(OUT/'HTTP_ZERO_PROOF.json',dict(**network,db_started=False,db_writes=0,source_persistence_calls=0,parser_changes=0,adapter_changes=0,run2=False))
    print('407shape audit/188source facts ready; semantic decisions still pending',flush=True)


if __name__=='__main__':prepare()
