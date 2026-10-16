"""Fresh read-only QA after accepted29. No ingestion or correction entrypoints."""
from pathlib import Path
import sys, xml.etree.ElementTree as ET
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import qa_ltt_uss_latest as q
from scripts.prepare_product_kind_29 import P, r, o
from scripts import product_kind_metadata_correction as correction
from scripts import ltt_uss_replay_seals as seals
OUT=r.BASE/'FULL_QA_PRODUCT_KIND29_2026-10-16'
PLAN=P/'EFFECTIVE_ROW_PLAN.json'
BASELINE=P/'REVISED_BASELINE_AFTER_PRODUCT_KIND_BATCH_FIX.json'

def history():
    correction.validate_gate();correction.validate_manifest(r.load(P/'PRODUCT_KIND_29_MANIFEST.json'))
    seals.checkpoint_provenance()
    seals.verify_active_code(baseline_seal=seals.PRE_KIND,proposed_transition=seals.KIND_PROPOSAL)
    for name,sha in r.load(P/'PRODUCT_KIND_CODE_SEAL.json').items():
        assert r.digest((o.ROOT/name).read_bytes())==sha,('CODE_DRIFT',name)
    for name,sha in r.load(P/'FINAL_ARTIFACT_SEAL.json').items():
        assert r.digest((P/name).read_bytes())==sha,('ACCEPTED_ARTIFACT_DRIFT',name)

q.OUT=OUT;q.PLAN=PLAN;q.BASELINE=BASELINE;q.history=history

def preflight():
    assert not OUT.exists(),'Fresh phase must not exist'
    history()
    cp=r.load(P/'FINAL_CHECKPOINT.json');run=r.load(P/'RUN2/RESULT.json');base=r.load(BASELINE)
    assert cp['status']=='LTT_USS_PRODUCT_KIND_BATCH_FIX_AND_RUN2_REVERIFIED' and cp['SAFE_TO_POWER_OFF']=='YES'
    assert run['scope']==2634 and run['exact_noop_persisted']==2453
    assert run['table_fingerprint_delta']==run['sequence_delta']==run['terminal_outcome_delta']==0
    assert base['totals']==dict(source_products=2453,offers=2453,supplier_http_captures=2533,offer_commercial_observations=2533)
    entries=r.load(PLAN)['rows'];assert len(entries)==len({(e['supplier'],e['index']) for e in entries})==2634
    for e in entries:assert r.digest((o.ROOT/e['journal']).read_bytes())==e['journal_sha256']
    git=q.git();assert git['branch'].strip()=='feature/universal-supplier-ltt-uss-ingestion' and git['HEAD'].strip()=='45052a6d21dae5e5f56e890f3a6e79466dcce733'
    r.save(OUT/'GIT_BEFORE.json',git)
    names=['PRODUCT_KIND_CODE_SEAL.json','REVISED_BASELINE_AFTER_PRODUCT_KIND_BATCH_FIX.json','PRODUCT_KIND_29_MANIFEST.json','PRODUCT_KIND_29_CORRECTION_RECEIPT.json','RUN2/RESULT.json','FINAL_CHECKPOINT.json','RECOVERY_NEXT.md','CHECKPOINT_PROVENANCE.json']
    r.save(OUT/'OFFLINE_PREFLIGHT.json',dict(status='PASS',accepted_artifact_hashes={n:r.digest((P/n).read_bytes()) for n in names},fresh_qa=True,previous_semantic_passes_not_carried=True,database_started=False,checked_at=r.now()))
    mods={c.attrib['classname'].split('.')[1] for fn in [q.accepted.P.parent/'FULL_QA_TERMINAL_BASELINE_2026-10-15/TESTS.xml',P/'FOCUSED_PROVENANCE_TESTS_POSTCOMMIT_VERIFICATION.xml',P/'REGRESSION_TESTS.xml',P/'METADATA_TESTS_GUARD_NORMALIZATION.xml'] for c in ET.parse(fn).findall('.//testcase')}
    r.atomic_bytes(OUT/'TEST_MODULES.txt',('\n'.join('tests/'+m+'.py' for m in sorted(mods))+'\n').encode())
    print('FRESH PREFLIGHT PASS, tests modules',len(mods),flush=True)

def structural():
    # Snapshot content unchanged; accepted RUN2 carries the source counters.
    derived=dict(r.load(BASELINE),sources=r.load(P/'RUN2/RESULT.json')['sources'])
    r.save(OUT/'BASELINE_WITH_SOURCE_COUNTERS.json',derived)
    q.BASELINE=OUT/'BASELINE_WITH_SOURCE_COUNTERS.json'
    q.structural()

def tests():
    root=ET.parse(OUT/'TESTS.xml');cases=root.findall('.//testcase')
    receipt=dict(total=len(cases),passed=sum(not any(c.find(t) is not None for t in ['failure','error','skipped']) for c in cases),fail=len(root.findall('.//failure')),error=len(root.findall('.//error')),skip=len(root.findall('.//skipped')))
    r.save(OUT/'TEST_RECEIPT.json',receipt);assert receipt['total']==receipt['passed'] and receipt['fail']==receipt['error']==receipt['skip']==0
    history();print(receipt,flush=True)

def packets():
    from scripts import qa_ltt_uss_latest_packets as packets
    # Reuse packet extraction only, all selection and decisions are fresh.
    from scripts.qa_ltt_uss_final_packets import source_packet
    mandatory={(a['supplier'],a['index']) for a in r.load(P/'PRODUCT_KIND_29_MANIFEST.json')['rows']}
    mandatory.update({('ltt_msk',i) for i in [64,358,359]});mandatory.update({('ltt_sib',i) for i in [941,958,959,1085]})
    mandatory.update({('ltt_yug',386),('uss_stanko',350)});mandatory.update({('uss_stanko',i) for i in q.FIXED})
    units={a[0] for a in r.load(BASELINE)['sib_units']};pop={s:[] for s in r.SOURCES}
    seed='FRESH_PRODUCT_KIND29_2026-10-16_V1\0'+r.digest(BASELINE.read_bytes())
    for e in r.load(PLAN)['rows']:
        if e['outcome']!='PERSISTED':continue
        dest=OUT/'PERSISTED_ROWS'/e['supplier']/(o.ROOT/e['journal']).name;db=r.load(dest);p=db['parsed_product']
        kind=p['enrichment_evidence']['product_kind'];assert db['offer']['raw_data']['enrichment_evidence']['product_kind']==kind==db['product']['raw_data']['enrichment_evidence']['product_kind']
        tags=[str(p[k]) for k in ['source_category','source_brand','availability']]+[p['price']['state'],p['price']['currency'],kind['product_kind']]
        must=(e['supplier'],e['index']) in mandatory or (e['supplier']=='ltt_sib' and e['external_id'] in units) or (e['supplier']=='ltt_yug' and e['external_id'] in ['312','297']) or p['price']['currency']=='USD'
        pop[e['supplier']].append(dict(**e,persisted_record=str(dest.relative_to(OUT)),tags=tags,mandatory=must,rank=r.digest((seed+'\0'+e['supplier']+'\0'+e['external_id']).encode())))
    chosen=[]
    for s,rows in pop.items():
        selected=[a for a in rows if a['mandatory']];rest=[a for a in rows if not a['mandatory']];freq=Counter(t for a in rows for t in a['tags']);covered={t for a in selected for t in a['tags']}
        while len(selected)<100:
            best=min(rest,key=lambda a:(-sum(1/freq[t] for t in a['tags'] if t not in covered),a['rank']));selected.append(best);rest.remove(best);covered.update(best['tags'])
        chosen.extend(sorted(selected,key=lambda a:a['rank']))
    assert len(chosen)==400
    r.save(OUT/'FRESH_400_SAMPLE_MANIFEST.json',dict(seed=seed,total=400,counts=Counter(e['supplier'] for e in chosen),rows=chosen,prior_passes_used=False,semantic_approval=False))
    with q.guard.guard() as network:
        for e in chosen:r.save(OUT/'SEMANTIC_PACKETS'/e['supplier']/(o.ROOT/e['journal']).name,source_packet(e))
        r.save(OUT/'PACKET_HTTP_PROOF.json',network)
    print('Fresh400 selected/packaged, no semantic approvals',flush=True)

if __name__=='__main__':{'preflight':preflight,'tests':tests,'structural':structural,'packets':packets}[sys.argv[1]]()
