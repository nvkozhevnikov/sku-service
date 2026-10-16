"""Fresh current-plan mappings and terminal source packets, no semantic carryover."""
from scripts import qa_final_hotspot36 as f
from scripts.verify_inverse97_resume import AUDIT
from scripts.qa_ltt_uss_final_packets import source_packet
from dataclasses import asdict
r,o,q,B,P,F=f.r,f.o,f.q,f.B,f.P,f.F
S,D,T=f.S,f.D,f.T

def main():
    assert r.load(F/'STRUCTURAL_AUDIT.json')['rows_checked']==2634
    plan={(e['supplier'],e['index']):e for e in r.load(q.PLAN)['rows']}
    cohorts={
      'VALID_MACHINE362':[a for a in r.load(r.BASE/'PRODUCT_KIND_IMPACT_SWEEP_2026-10-15/PRODUCT_KIND_SEMANTIC_DECISIONS.json')['rows'] if a['semantic_decision']=='VALID_MACHINE'],
      'OPERATOR106':[a for a in r.load(AUDIT/'INVERSE_KIND_SEMANTIC_DECISIONS.json')['rows'] if a['semantic_decision']=='OPERATOR_REVIEW_REQUIRED'],
      'INVERSE97':r.load(B/'INVERSE_KIND_97_MANIFEST.json')['rows'],
      'PREVIOUS29':r.load(P/'PRODUCT_KIND_29_MANIFEST.json')['rows'],
      'DISK10':r.load(T/'DISK_ALIAS_10_MANIFEST.json')['rows'],
      'HOTSPOT36':r.load(S/'FINAL_HOTSPOT36_MANIFEST.json')['rows'],
      'TRUE_DISK137':r.load(D/'DISK_ALIAS_VALID_REGRESSION_COHORT.json')['rows']}
    with q.guard.guard() as network:
      for label,rows in cohorts.items():
        checked=[]
        for original in rows:
          a=dict(original,**original['entry']) if 'entry' in original else original
          e=plan[a['supplier'],a['index']];j=r.load(o.ROOT/e['journal']);m=j['result']
          assert e['external_id']==a['external_id'] and e['url']==a['url'] and m['evidence_sha256']==a['evidence_sha256']
          db=r.load(F/'PERSISTED_ROWS'/e['supplier']/(o.ROOT/e['journal']).name)
          kind=db['parsed_product']['enrichment_evidence']['product_kind']
          expected=a.get('new_kind') or a.get('current_kind') or a['raw_enrichment_evidence']['product_kind']
          assert kind==expected==db['product']['raw_data']['enrichment_evidence']['product_kind']==db['offer']['raw_data']['enrichment_evidence']['product_kind']
          checked.append(dict(entry=e,evidence_sha256=m['evidence_sha256'],current_kind=kind,source_product_id=db['product']['id'],offer_id=db['offer']['id'],status='CURRENT_MAPPING_PASS',fresh_semantic_approval=False))
        assert len(checked)=={'VALID_MACHINE362':362,'OPERATOR106':106,'INVERSE97':97,'PREVIOUS29':29,'DISK10':10,'TRUE_DISK137':137,'HOTSPOT36':36}[label]
        r.save(F/(label+'_MAPPING.json'),dict(status='PASS',count=len(checked),rows=checked,prior_semantic_passes_used=False))
      terminal=[]
      for index in [50,51,182,221,191,194,195,196,197,225]:
        e=plan['uss_stanko',index];j=r.load(o.ROOT/e['journal']);m=j['result'];assert e['outcome']!='PERSISTED'
        body=(r.OUT/'EVIDENCE/uss_stanko'/(m['evidence_sha256']+'.html')).read_bytes()
        product=r.parse('uss_stanko',body,m['final_url']);kind=asdict(product)['enrichment_evidence']['product_kind']
        assert kind['product_kind']==('machine' if index in {50,51,182,221} else ('unknown' if index==225 else 'cutting_tool')) and kind['confidence']==('UNKNOWN' if index==225 else 'HIGH')
        assert kind==r.load(S/'PARSED_ROWS/uss_stanko'/f'{index:06d}.json')['parsed_product']['enrichment_evidence']['product_kind']
        packet=source_packet(e);r.save(F/'TERMINAL_KIND_PACKETS'/f'{index:06d}.json',packet)
        terminal.append(dict(entry=e,title=product.name,kind=kind,outcome=e['outcome'],evidence_sha256=m['evidence_sha256'],semantic_decision=None))
      r.save(F/'TERMINAL_KIND10_FRESH.json',dict(count=10,rows=terminal,status='FRESH_PACKETS_REQUIRE_REVIEW',prior_semantic_passes_used=False))
      from scripts import qa_ltt_uss_latest_terminals as t
      t.OUT=F;t.audit()
      r.save(F/'COHORTS_HTTP_ZERO_PROOF.json',network)
    print('Fresh mappings362/106/97/29 and terminal180/9 source packets complete; semantic review pending',flush=True)

if __name__=='__main__':main()
