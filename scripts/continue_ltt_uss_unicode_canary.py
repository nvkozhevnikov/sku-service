"""Explicit continuation: preserve MSK/36 successes, replace only failed SIB retry."""
from pathlib import Path
from datetime import datetime,timezone
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import run_ltt_uss_live_canary as runner
from scripts.run_ltt_uss_offline_canary import save

BASE=Path(__file__).resolve().parents[1]/'reports/LTT_USS_INTEGRATION_2026-10-13'
NEW=BASE/'UNICODE_RESUME_2026-10-14'


if __name__=='__main__':
    current=NEW/'LIVE_CANARY.json'
    if current.exists():
        raise RuntimeError('Explicit transition already exists; inspect before repeating')
    old=json.loads((BASE/'RESUME_2026-10-14/LIVE_CANARY.json').read_text('utf-8'))
    retry=json.loads((NEW/'RETRY.json').read_text('utf-8'))
    assert retry['status']=='PASS' and retry['source_identity_unchanged']
    assert old['sources']['ltt_msk']['status']=='LIVE_CANARY_PASS'
    rows=[]
    for row in old['rows']:
        if row['requested_url']==old['failure_row']['requested_url']:
            assert row['outcome']=='NETWORK_ERROR'
            replacement={k:v for k,v in retry.items() if k not in {'previous_failure','after_fingerprints'}}
            replacement['evidence_root']='UNICODE_RESUME_2026-10-14/EVIDENCE'
            rows.append(replacement)
        else:
            assert row['outcome']=='PERSISTED'
            row['evidence_root']='RESUME_2026-10-14/EVIDENCE'
            rows.append(row)
    assert len(rows)==37 and len({(r['supplier'],r['requested_url']) for r in rows})==37
    state=dict(started_at=datetime.now(timezone.utc).isoformat(),status='RUNNING',plans=old['plans'],
               sources={'ltt_msk':old['sources']['ltt_msk']},rows=rows,supplier_gets=38,
               prior_attempt_history=[old['failure_row']],completed_rows_replayed=0,
               transition='OPERATOR_AUTHORIZED_FAILED_ROW_ONLY_RETRY_THEN_PENDING_ROWS',
               run1_started=False,run2_started=False,full_qa_started=False)
    assert state['plans']==runner.plan()
    save(current,state)
    runner.OUT=NEW
    raise SystemExit(runner.main())
