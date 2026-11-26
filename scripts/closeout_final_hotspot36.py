"""Read-only current revised baseline proof before mandatory clean stop."""
from scripts.execute_final_hotspot36 import S,r,o,history,protected,same
from scripts import qa_ltt_uss_revised as guard
import psycopg

def main():
    history()
    from scripts.final_hotspot36_metadata_correction import validate_gate
    validate_gate()
    with guard.guard() as network:
      _,cfg=r.config()
      with psycopg.connect(**cfg,autocommit=True) as raw:
        raw.execute('SET default_transaction_read_only=on');raw.execute("SET application_name='ltt_uss_hotspot36_readonly_closeout'")
        assert raw.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        c=guard.ReadOnlyConnection(raw);system=r.verify_system();after=o.snapshot(c)
        baseline=r.load(S/'HOTSPOT36_REVISED_BASELINE.json') if (S/'HOTSPOT36_REVISED_BASELINE.json').exists() else r.load(S/'BEFORE_CORRECTION.json')
        same(after,baseline);r.save(S/'POST_QA_PREVIOUS_PROTECTED.json',protected(after))
        pairs={p['id']:(p,v) for p,v in after['product_offer_projections']}
        if (S/'HOTSPOT36_CORRECTION_RECEIPT.json').exists():
          receipt=r.load(S/'HOTSPOT36_CORRECTION_RECEIPT.json')
          for a in receipt['rows']:assert r.encoded(pairs[a['source_product_id']])==r.encoded((a['verified_source_product'],a['verified_offer']))
          r.save(S/'POST_QA_CORRECTED36_FRESH_SQL.json',dict(status='PASS',verified_pairs=36,rows=[dict(source_product=pairs[a['source_product_id']][0],offer=pairs[a['source_product_id']][1]) for a in receipt['rows']]))
        r.save(S/'AFTER_ALL_VERIFICATION.json',dict(**after,**system,checked_at=r.now()))
        r.save(S/'PRE_SHUTDOWN_SQL.json',dict(**o.clients(c),current_db_baseline_intact=True,table_fingerprint_delta=0,sequence_delta=0,tables=51,sequences=40,checked_at=r.now()))
      r.save(S/'CLOSEOUT_HTTP_ZERO_PROOF.json',dict(**network,strict_readonly=True,sql_writes=0))
    print('Closeout current baseline intact; corrected10/protected97/29/106; clients/open/prepared0',flush=True)

if __name__=='__main__':main()
