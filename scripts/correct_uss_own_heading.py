"""Scoped own-heading audit/correction. Historical phase evidence is immutable."""
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from datetime import datetime
import copy
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bs4 import BeautifulSoup
from scripts import correct_uss_commercial_tables as f
r, o = f.r, f.o
PHASE = r.BASE/'USS_H2_FIX_2026-10-15'
PLAN = f.PHASE/'EFFECTIVE_ROW_PLAN.json'
ADAPTER = Path(f.ADAPTER)


def effective_rows():
    for entry in r.load(PLAN)['rows']:
        path = o.ROOT/entry['journal']
        assert r.digest(path.read_bytes()) == entry['journal_sha256']
        yield path, r.load(path)


def audit():
    assert not (PHASE/'TITLE_AUDIT_BEFORE.json').exists()
    assert r.code_hashes() == r.load(f.PHASE/'NEW_CODE_SEAL.json')
    f.verify_history()
    r.atomic_bytes(PHASE/'OLD_USS_ADAPTER.py', ADAPTER.read_bytes())
    r.save(PHASE/'OLD_CODE_SEAL.json', r.code_hashes())
    rows = []
    with o.offline_guard() as network:
      for path, row in effective_rows():
        if row['supplier'] != 'uss_stanko': continue
        body, status, diagnostics, outcome = f.outcome_for(row)
        assert outcome == row['outcome']
        p = r.parse('uss_stanko', body, row['result']['final_url'])
        d = BeautifulSoup(body.decode('utf8'), 'html.parser')
        headings = [dict(tag=h.name, classes=h.get('class', []), text=h.get_text(' ',strip=True),
                         parent_tag=h.parent.name,parent_id=h.parent.get('id')) for h in d.select('h1,h2')]
        tables=[]
        for t in d.select('table.data'):
            from universal_supplier.adapters.ltt_uss_html import data_rows
            cells = list(data_rows(t))
            models=[v for v in cells if v and v[0].lower()=='модель']
            articles=[v for v in cells if v and v[0].lower() in {'номер арт.','артикул'}]
            prices=[v for v in cells if v and v[0].lower().startswith('цена')]
            stocks=[v for v in cells if v and v[0].lower()=='наличие']
            tables.append(dict(rows=cells,models=models,articles=articles,prices=prices,stocks=stocks,
                article_anchors=[a.get('name') for a in t.select('a[name^="art"]')],
                purchase_controls=[str(a) for a in t.select('a.bnt_to_cart,a.bnt_zak')],html=str(t),
                parent_tag=t.parent.name,parent_id=t.parent.get('id')))
        own=d.select('h2.zagolovok_u')
        classification = 'OTHER'
        reason = 'Existing h1 payload retained' if p.name else 'No uniquely proven own h2 product shape'
        if outcome=='MULTI_VARIANT': classification='MULTI_VARIANT';reason='Existing multi-variant gate; unchanged'
        elif not p.name: classification='LEGITIMATE_AMBIGUOUS'
        # Independent evidence decision before implementing any fallback.
        confirmed=False
        if not d.find('h1') and len(own)==1 and len(tables)==1:
            h,t=own[0],d.select_one('table.data');a=tables[0]
            norm=lambda s: ''.join(c for c in s.casefold() if c.isalnum())
            groups=[a[k] for k in ['models','articles','prices','stocks']]
            coherent=all(len(g)==1 and len(g[0])==2 and g[0][1] for g in groups)
            canonical=[c.get('href') for c in d.select('link[rel="canonical"]')]
            if coherent:
                model,article=a['models'][0][1],a['articles'][0][1]
                confirmed=(outcome=='AMBIGUOUS' and h.parent is t.parent and h.parent.get('id') in {'div_print','div_body'}
                    and len(h.parent.select('h1,h2'))==1 and norm(model) in norm(h.get_text(' ',strip=True))
                    and a['article_anchors']==['art'+norm(article)] and len(a['purchase_controls'])==1
                    and canonical==[row['url']]==[row['result']['final_url']]
                    and list(h.parent.children).index(h)<list(t.parent.children).index(t))
            if confirmed:
                classification='CONFIRMED_TITLE_COVERAGE_DEFECT'
                reason='Self canonical; one own h2 before one coherent model/article/price/stock table and matching article anchor/purchase control in same div_print/div_body; no variants'
        if row['index']==405:
            assert len(tables)>1
            reason='Own heading plus additional accessory product table/purchase controls; conservative multi-product boundary is not proven for generic fallback; preserve AMBIGUOUS'
        rows.append(dict(index=row['index'],source_id=p.site_internal_id,url=row['url'],effective_outcome=outcome,
            journal=str(path.relative_to(o.ROOT)),journal_sha256=r.digest(path.read_bytes()),
            evidence_sha256=row['result']['evidence_sha256'],response_sha256=row['result']['response_sha256'],
            original_time=row['result']['observed_at'],headings=headings,h1_count=len(d.select('h1')),
            own_h2=[str(h) for h in own],document_title=d.title.get_text(' ',strip=True) if d.title else None,
            canonicals=[c.get('href') for c in d.select('link[rel="canonical"]')],tables=tables,
            commercial_table_count=len(tables),purchase_control_count=len(d.select('a.bnt_to_cart,a.bnt_zak')),
            variant_evidence='MULTI_VARIANT' in p.diagnostics,current_name=p.name,old_payload=asdict(p),
            old_payload_sha256=r.digest(r.encoded(asdict(p))),expected_own_heading=own[0].get_text(' ',strip=True) if confirmed else p.name,
            classification=classification,reason=reason,expected_new_outcome='PERSISTED' if confirmed else outcome))
    report=dict(status='ALL_407_TITLE_ROWS_AUDITED_BEFORE_CHANGE',rows=rows,network=network,checked_at=r.now())
    assert len(rows)==407
    r.save(PHASE/'TITLE_AUDIT_BEFORE.json',report)
    candidates=[a for a in rows if a['index'] in {368,369,374,377,384,405}]
    r.save(PHASE/'KNOWN_CANDIDATE_DECISIONS.json',candidates)
    print([(a['index'],a['classification'],a['commercial_table_count']) for a in candidates],flush=True)
    print(Counter(a['classification'] for a in rows),flush=True)


def blocked_closeout():
    """Read-only impact accounting after failed focused gate; no retry or DB."""
    from xml.etree import ElementTree as ET
    before=r.load(PHASE/'TITLE_AUDIT_BEFORE.json')
    changes=[];gate_counts=Counter();title_counts=Counter();changed_outcomes=Counter()
    with o.offline_guard() as network:
      for a in before['rows']:
        body=(r.OUT/'EVIDENCE/uss_stanko'/(a['evidence_sha256']+'.html')).read_bytes()
        assert r.digest(body)==a['evidence_sha256']
        p=r.parse('uss_stanko',body,a['url']);payload=asdict(p)
        title_counts['H1_TITLE_ROWS' if a['h1_count'] and p.name else ('H2_OWN_TITLE_ROWS' if p.name else 'NO_VALID_TITLE_ROWS')]+=1
        status,diags=f._card_gate('uss_stanko',body.decode('utf8'),a['url'])
        outcome='PERSISTED' if status.value=='SUCCESS' else ('MULTI_VARIANT' if 'MULTI_VARIANT' in diags else status.value)
        gate_counts[outcome]+=1
        if r.digest(r.encoded(payload))!=a['old_payload_sha256']:
            changed_outcomes[a['effective_outcome']]+=1
            changes.append(dict(index=a['index'],url=a['url'],source_id=p.site_internal_id,
                classification=a['classification'],old_outcome=a['effective_outcome'],actual_new_gate_outcome=outcome,
                actual_gate_diagnostics=diags,old_payload=a['old_payload'],new_payload=payload,
                new_payload_sha256=r.digest(r.encoded(payload)),original_time=a['original_time'],
                evidence_sha256=a['evidence_sha256'],response_sha256=a['response_sha256']))
      f.verify_history()
    impact=dict(USS_TOTAL=407,**title_counts,CONFIRMED_TITLE_COVERAGE_DEFECTS=sum(a['classification']=='CONFIRMED_TITLE_COVERAGE_DEFECT' for a in before['rows']),
        REVIEW_ONLY=sum(a['classification']=='LEGITIMATE_AMBIGUOUS' for a in before['rows']),
        PERSISTED_ROWS_CHANGED_BY_NEW_PARSER=changed_outcomes['PERSISTED'],
        AMBIGUOUS_ROWS_CHANGED_BY_NEW_PARSER=changed_outcomes['AMBIGUOUS'],
        MULTI_VARIANT_ROWS_CHANGED_BY_NEW_PARSER=changed_outcomes['MULTI_VARIANT'],
        actual_gate_outcomes=gate_counts,changed_rows=[a['index'] for a in changes],network=network,
        status='BLOCKED_FOCUSED_TEST_GATE_FAILED_NO_DB',checked_at=r.now())
    assert changed_outcomes==Counter(AMBIGUOUS=5)
    assert {a['index'] for a in changes}=={368,369,374,377,384}
    r.save(PHASE/'IMPACT_AFTER_LOCAL_FIX.json',impact)
    r.save(PHASE/'ALL_CHANGED_PAYLOADS.json',changes)
    r.save(PHASE/'AFFECTED_ROWS.json',[dict(index=a['index'],source_id=a['source_id'],url=a['url'],
        title=a['new_payload']['name'],model=a['new_payload']['supplier_model'],article=a['new_payload']['manufacturer_article'],
        price=a['new_payload']['price'],availability=a['new_payload']['availability'],
        original_time=a['original_time'],response_sha256=a['response_sha256'],evidence_sha256=a['evidence_sha256'],
        old_outcome=a['old_outcome'],intended_outcome='PERSISTED',actual_gate_outcome=a['actual_new_gate_outcome'],db_applied=False) for a in changes])
    suite=ET.parse(PHASE/'FOCUSED_TESTS.xml').getroot().find('testsuite')
    receipt=dict(total=int(suite.attrib['tests']),fail=int(suite.attrib['failures']),error=int(suite.attrib['errors']),skip=int(suite.attrib['skipped']),
        regression_run=False,retest_run=False,short_temp_root=str(o.ROOT.parents[1]/'tmp/lttqa'))
    receipt['pass']=receipt['total']-receipt['fail']-receipt['error']-receipt['skip']
    r.save(PHASE/'TEST_RECEIPT.json',receipt)
    r.save(PHASE/'NEW_CODE_SEAL.json',r.code_hashes())
    r.save(PHASE/'TRANSITION.json',dict(OLD='H1_ONLY_USS_TITLE_EXTRACTION',NEW='EVIDENCE_BACKED_OWN_HEADING_EXTRACTION',
        ROOT_CAUSE='VERIFIED_TITLE_SELECTOR_COVERAGE_DEFECT',status='LOCAL_ADAPTER_CHANGE_NOT_ACCEPTED_TEST_GATE_FAILED',
        old_code_seal=r.load(PHASE/'OLD_CODE_SEAL.json'),new_code_seal=r.code_hashes(),
        historical_evidence_unchanged=True,tests='TEST_RECEIPT.json',impact='IMPACT_AFTER_LOCAL_FIX.json',affected='AFFECTED_ROWS.json'))
    blocker=dict(status='LTT_USS_H2_TITLE_FIX_BLOCKED',stage='FOCUSED_TEST_GATE',row=368,
        blocker='Adapter extracts own h2 with source_url, but _card_gate invokes USS parser without source_url; self-canonical comparison unavailable and fallback fails closed',
        root_cause='SOURCE_URL_NOT_FORWARDED_BY_EXISTING_USS_CARD_GATE',confirmed_affected_scope=[a['index'] for a in changes],
        tests=receipt,db_correction_applied=False,database_started=False,backfill_run=False,revised_baseline_created=False,new_run2_run=False,
        supplier_http=0,network=network,baseline_totals=r.load(f.PHASE/'REVISED_RUN1_BASELINE_AFTER_QA_FIX.json')['totals'],
        baseline_claim='PACKAGED-EVIDENCE_UNCHANGED_NO_DATABASE_START',current_uss=dict(PERSISTED=214,AMBIGUOUS=127,MULTI_VARIANT=66,pending=0),
        full_qa_resumed=False,finalizer_started=False,matching_run=False,offer_selection_run=False,sterbrust_writes=0,esol_writes=0,
        next='OPERATOR_REVIEW_FAILED_FOCUSED_GATE_AND_AUTHORIZE_RESUME',safe_to_power_off=False,checked_at=r.now())
    r.save(PHASE/'BLOCKER.json',blocker);r.save(PHASE/'CHECKPOINT.json',blocker)
    r.save(PHASE/'HTTP_ZERO_PROOF.json',network)
    print(r.encoded(dict(impact=impact,tests=receipt)).decode(),flush=True)


if __name__=='__main__':
    (blocked_closeout if len(sys.argv)>1 and sys.argv[1]=='blocked-closeout' else audit)()
