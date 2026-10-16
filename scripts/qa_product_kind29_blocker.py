"""Record the first fresh semantic defect, then read-only closeout only."""
from scripts.qa_product_kind29_fresh import *
from scripts.qa_product_kind29_review import facts
from bs4 import BeautifulSoup
from dataclasses import asdict
from sterbrust_matching.product_identity import classify_product_kind
import psycopg,re

def main():
    selected=r.load(OUT/'FRESH_400_SAMPLE_MANIFEST.json')['rows'];e=selected[7]
    assert e['supplier']=='ltt_msk' and e['index']==82 and e['external_id']=='1745'
    record=r.load(OUT/e['persisted_record']);packet=r.load(OUT/'SEMANTIC_PACKETS'/e['supplier']/(o.ROOT/e['journal']).name)
    journal=r.load(o.ROOT/e['journal']);m=journal['result'];body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes()
    assert r.digest(body)==m['evidence_sha256']==packet['evidence_sha256']
    dom=BeautifulSoup(body.decode('utf8'),'html.parser');description=dom.select_one('.product [itemprop="description"]')
    assert description is not None
    own=' '.join(description.get_text(' ',strip=True).split());p=record['parsed_product']
    core=asdict(classify_product_kind(p['name'],p['source_category'],own_description=own))
    assert core['product_kind']=='machine'
    source=record['product'];offer=record['offer'];actual=p['enrichment_evidence']['product_kind']
    assert actual==source['raw_data']['enrichment_evidence']['product_kind']==offer['raw_data']['enrichment_evidence']['product_kind']
    assert actual['product_kind']=='spare' and actual['confidence']=='HIGH'
    assert re.search(r'\bролик\w*',p['name'].lower()).group()=='роликовой'
    assert 'Предназначен для штучного и пакетного раскроя' in own and 'независимые двигатели' in own
    witness=dict(source=e['supplier'],row=82,source_id='1745',url=e['url'],title=p['name'],own_description=own,
        own_title=packet['own_title'],stable_ids=packet['stable_ids'],price_cells=packet['numeric_price_cells'],stock_spans=packet['stock_spans'],
        evidence_sha256=m['evidence_sha256'],response_sha256=m['response_sha256'],original_observed_at=m['observed_at'],
        source_product_id=source['id'],offer_id=offer['id'],capture_id=record['capture']['id'],observation_id=record['observation']['id'],
        expected='Complete machine; machine family, not spare',shared_classifier=core,current_parser_kind=actual,source_kind=source['raw_data']['enrichment_evidence']['product_kind'],offer_kind=offer['raw_data']['enrichment_evidence']['product_kind'],
        mechanism='source_evidence classifies core as machine, then unconditional spare regex ролик\\w* matches adjective роликовой in complete-machine title and overwrites it with spare/HIGH.',
        responsible_path='universal_supplier/adapters/ltt_uss_html.py',responsible_lines=[78,79,80],unchanged_commercial=dict(price='319000',currency='RUB',availability='InStock',raw_availability='заканчиваются'),
        source_class='PACKAGED-EVIDENCE',code_class='REPOSITORY-VERIFIED',semantic_class='INFERENCE_FROM_EXPLICIT_OWN_SOURCE',prior_passes_used=False)
    r.save(OUT/'BLOCKER_EVIDENCE.json',witness)
    notes={73:'Complete MJ6132C panel saw with electric lifting; own title/ID and507000RUB/InStock agree.',41:'Radius cutter40mm sells tooling, not host machine;2500RUB/InStock agrees.',277:'Complete MB108HL thicknesser with electric table lift;457000RUB/OutOfStock agrees.',148:'Complete HX60A brush sanding machine, grinder family valid;92000RUB/InStock agrees.',357:'Complete MF258 knife sharpening machine; host knives are workpieces, grinder correct;190000RUB/InStock agrees.',42:'Own saw disk205x45 sold as disk tooling, not circular saw machine;2800RUB/OutOfStock agrees.',208:'Complete MB524L jointer with shaft/table, machine correct;205000RUB/OutOfStock agrees.'}
    results=[]
    for i,a in enumerate(selected):
        row=dict(supplier=a['supplier'],index=a['index'],external_id=a['external_id'],url=a['url'],status='PASS' if i<7 else 'FAIL' if i==7 else 'NOT_REVIEWED_AFTER_FIRST_MATERIAL_BLOCKER')
        if i<=7:
            f=facts.read(a);assert not f['issues']
            row.update(source_comparison=f,reason=notes[a['index']] if i<7 else witness['mechanism'],prior_passes_used=False)
        results.append(row)
    r.save(OUT/'FRESH_400_SEMANTIC_RESULTS.json',dict(status='BLOCKED_FIRST_MATERIAL_DEFECT',selected=400,reviewed=8,passed=7,failed=1,unreviewed=392,reviewed_by_supplier=dict(ltt_msk=8,ltt_sib=0,ltt_yug=0,uss_stanko=0),rows=results,
        batch_fact_extraction=50,batch_extraction_after_first_failure_is_not_review_or_approval=True))
    pending=dict(status='NOT_REACHED_AFTER_FIRST_MATERIAL_BLOCKER',product_kind29_fresh_semantic=0,valid_machine_mapping=362,valid_machine_fresh_subset=0,terminal_kind_fresh=5,historical_uss_fresh=0,sib_units_fresh=0,
        yug_and_usd_automated_trace_pass=True,yug_and_usd_fresh_semantic=False,prior_acceptance_not_substituted=True)
    r.save(OUT/'REMAINING_ACCEPTANCE_GATES.json',pending)
    blocker=dict(status='LTT_USS_FULL_QA_BLOCKED',source='ltt_msk',row=82,source_id='1745',url=e['url'],qa_layer='FRESH_PERSISTED_SEMANTIC_PRODUCT_KIND_COMPLETE_MACHINE_FALSE_DOWNGRADE',blocker='Complete panel saw is persisted as spare/HIGH',
        expected=witness['expected'],actual=actual,confirmed_scope=[dict(supplier='ltt_msk',index=82,external_id='1745',source_product_id=142,offer_id=142)],
        wider_scope='UNKNOWN; not investigated after mandatory STOP. Shared override may affect other complete-machine titles; this is inference, not confirmed impact.',db_correction_required=True,
        db_correction_scope='Derived product_kind in existing source_product142 and offer142; no correction performed or authorized in this QA.',supplier_http=0,sql_writes=0,source_repairs=False,
        structural_rows_checked=2634,persisted_automated_checked=2453,semantic_selected=400,semantic_reviewed=8,semantic_pass=7,semantic_fail=1,semantic_unreviewed=392,
        terminal_mapping_ambiguous=114,operator_fresh=6,terminal_mapping_multi=66,multi_risk_fresh=9,terminal_kind_fresh=5,valid_machine_mapping=362,product_kind29_fresh_semantic=0,
        tests=r.load(OUT/'TEST_RECEIPT.json'),full_qa_verified=False,finalizer_started=False,matching_run=False,selection_run=False,sterbrust_writes=0,esol_writes=0,SAFE_TO_POWER_OFF='PENDING',checked_at=r.now())
    r.save(OUT/'BLOCKER.json',blocker);r.save(OUT/'CHECKPOINT.json',blocker)
    with q.guard.guard() as network:
        _,cfg=r.config()
        with psycopg.connect(**cfg,autocommit=True) as raw:
            raw.execute('SET default_transaction_read_only=on');raw.execute("SET application_name='ltt_uss_kind29_qa_blocker_readonly_closeout'");raw.execute("SET statement_timeout='30s'")
            assert raw.execute('SHOW transaction_read_only').fetchone()[0]=='on'
            c=q.guard.ReadOnlyConnection(raw);after=o.snapshot(c);before=r.load(OUT/'BEFORE_QA.json')
            assert all(r.encoded(before[k])==r.encoded(v) for k,v in after.items())
            pairs=c.execute('''SELECT row_to_json(p),row_to_json(o) FROM source_products p JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id WHERE s.code=%s AND p.external_id=%s''',('ltt_msk','1745')).fetchall()
            assert len(pairs)==1 and r.encoded(pairs[0])==r.encoded([source,offer])
            clients=o.clients(c)
            r.save(OUT/'BLOCKER_DB_VERIFICATION.json',dict(rows=pairs,read_only=True,clients=clients,db_correction_applied=False,checked_at=r.now()))
            r.save(OUT/'AFTER_QA.json',dict(**after,checked_at=r.now()))
            r.save(OUT/'PROTECTED_STATE_COMPARISON.json',dict(status='PASS',tables_compared=51,sequences_compared=40,table_fingerprint_delta=0,sequence_delta=0,supplier_namespace_delta=0,foreign_mutations=0,source_db_writes=0,clients=clients))
            r.save(OUT/'PRE_SHUTDOWN_SQL.json',dict(clients=clients,read_only=True,checked_at=r.now()))
            r.save(OUT/'HTTP_ZERO_PROOF.json',dict(**network,sql_writes=0,source_persistence_calls=0,read_only_sql_enforced=True))
    history();r.save(OUT/'GIT_AFTER_QA.json',q.git())
    print('BLOCKED MSK82/1745; sample8/400 PASS7 FAIL1 unreviewed392; closeout51tables/40seq delta0; clients/transactions0',flush=True)

if __name__=='__main__':main()
