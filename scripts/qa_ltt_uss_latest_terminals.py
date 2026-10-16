"""Fresh mapping and DOM consistency facts; spot-check facts need review."""
from bs4 import BeautifulSoup
import re
from scripts import qa_ltt_uss_latest as q
r,o,OUT=q.r,q.o,q.OUT

def text(n):return ' '.join(n.get_text(' ',strip=True).split()) if n is not None else ''

def facts(dom):
    block=dom.select_one('#div_print') or dom.select_one('#div_body') or dom
    tables=[]
    for t in block.find_all('table'):
        if t.find('table'):continue
        rows=[]
        for tr in t.find_all('tr'):
            cells=tr.find_all(['td','th'],recursive=False)
            if cells and not any(c.find('table') for c in cells):rows.append([text(c) for c in cells])
        anchors=[a.get('name') for a in t.select('a[name^="art"]')]
        controls=[dict(text=text(a),onclick=a.get('onclick')) for a in t.select('[onclick]') if re.match(r'\s*(add_to_cart|zak)\(',a.get('onclick',''))]
        if anchors or controls or 'data' in t.get('class',[]):tables.append(dict(rows=rows,article_anchors=anchors,controls=controls,html=str(t)))
    return dict(headings=[dict(tag=h.name,text=text(h)) for h in dom.select('h1,h2')],body_text=text(block),tables=tables,
        article_anchors=[a.get('name') for a in block.select('a[name^="art"]')],controls=[a.get('onclick') for a in block.select('[onclick]') if re.match(r'\s*(add_to_cart|zak)\(',a.get('onclick',''))])

def audit():
    assert not (OUT/'AMBIGUOUS_DECISION_VERIFICATION.json').exists()
    inventory=r.load(q.accepted.P/'REVISED_TERMINAL_INVENTORY.json')['rows'];decisions={a['index']:a for a in inventory}
    plan=[e for e in r.load(q.PLAN)['rows'] if e['supplier']=='uss_stanko'];terminal=[e for e in plan if e['outcome']!='PERSISTED']
    assert len(decisions)==len(inventory)==len(terminal)==180 and set(decisions)=={e['index'] for e in terminal}
    amb=[];multi=[];risk=[];operator=[]
    risky={27,50,86,131,182,285,300,317,328}
    with q.guard.guard() as network:
      for e in terminal:
        path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256'];row=r.load(path);m=row['result'];d=decisions[e['index']]
        raw,body=q.accepted.b.saved(row);dom=BeautifulSoup(body.decode('utf8'),'html.parser')
        outcome,p,capture=o.extract(row);assert outcome==e['outcome']
        assert d['outcome']==outcome and d['url']==e['url'] and d['decision_evidence']['evidence_sha256']==m['evidence_sha256']
        assert r.digest(raw)==m['response_sha256'] and r.digest(body)==m['evidence_sha256']
        assert d['decision'].startswith('VALID_') and d['reason']
        record=dict(index=e['index'],url=e['url'],source_id=d['source_id'],current_outcome=outcome,prior_decision=d['decision'],prior_reason=d['reason'],evidence_sha256=m['evidence_sha256'],response_sha256=m['response_sha256'],decision_mapping='PASS',fresh_semantic_recheck=False)
        (multi if outcome=='MULTI_VARIANT' else amb).append(record)
        if e['index'] in q.accepted.b.OPERATOR or e['index'] in risky:
            f=facts(dom);dest=OUT/'TERMINAL_FRESH_FACTS'/f"{e['index']:06d}.json";r.save(dest,dict(**record,source_facts=f))
            witness=dict(index=e['index'],facts=str(dest.relative_to(OUT)),sha256=r.digest(dest.read_bytes()),headings=f['headings'],models=[v for t in f['tables'] for v in t['rows'] if v and v[0].lower()=='модель'],prices=[v for t in f['tables'] for v in t['rows'] if v and v[0].lower().startswith('цена')],stocks=[v for t in f['tables'] for v in t['rows'] if v and v[0].lower()=='наличие'],article_count=len(f['article_anchors']),controls=len(f['controls']),semantic_decision=None)
            (operator if e['index'] in q.accepted.b.OPERATOR else risk).append(witness)
        if len(amb)+len(multi) in {60,120,180}:print(f'CURRENT TERMINAL MAPPING {len(amb)+len(multi)}/180',flush=True)
    assert len(amb)==114 and len(multi)==66
    assert {x['index'] for x in operator}==q.accepted.b.OPERATOR
    assert next(e for e in plan if e['index']==405)['outcome']=='PERSISTED'
    r.save(OUT/'AMBIGUOUS_DECISION_VERIFICATION.json',dict(mapping_status='PASS',count=114,missing=0,duplicate=0,stale_decisions=0,rows=amb,operator_recheck_status='PENDING',operator_facts=operator,network=network))
    r.save(OUT/'MULTI_VARIANT_DECISION_VERIFICATION.json',dict(mapping_status='PASS',count=66,missing=0,duplicate=0,stale_decisions=0,rows=multi,risk_semantics_status='PENDING',risk_facts=risk,network=network))
    print('114/66 mapping and currentoutcomes PASS; sixoperator/ninerisk freshfacts require semanticreview',flush=True)

if __name__=='__main__':audit()
