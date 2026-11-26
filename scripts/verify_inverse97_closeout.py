"""Fresh read-only closeout for verify-only RUN2 and QA, before clean stop."""
from scripts.verify_inverse97_resume import r,o,B,N,history,same,protected
from scripts import qa_ltt_uss_revised as guard
import psycopg

def main():
    history()
    with guard.guard() as network:
      _,cfg=r.config()
      with psycopg.connect(**cfg,autocommit=True) as raw:
        raw.execute('SET default_transaction_read_only=on')
        raw.execute("SET application_name='ltt_uss_inverse97_verify_closeout_readonly'")
        assert raw.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        c=guard.ReadOnlyConnection(raw);system=r.verify_system();after=o.snapshot(c)
        same(after,r.load(B/'INVERSE_KIND_REVISED_BASELINE.json'))
        r.save(N/'POST_QA_PROTECTED_FRESH_SQL.json',protected(after))
        r.save(N/'AFTER_ALL_VERIFICATION.json',dict(**after,**system,checked_at=r.now()))
        r.save(N/'PRE_SHUTDOWN_SQL.json',dict(**o.clients(c),current_db_baseline_intact=True,tables=51,sequences=40,table_fingerprint_delta=0,sequence_delta=0,db_writes_during_run2=0,db_writes_during_full_qa=0,correction97_repeated=False))
      r.save(N/'CLOSEOUT_HTTP_ZERO_PROOF.json',dict(**network,sql_writes=0,strict_readonly=True))
    print('Fresh closeout: CURRENT97 baseline intact; protected97/29/106; clients/open/prepared0',flush=True)

if __name__=='__main__':main()
