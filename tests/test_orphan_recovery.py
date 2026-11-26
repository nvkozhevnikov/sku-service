from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from contextlib import nullcontext
import json
import pytest
from competitor_intel.orphan_recovery import recover_orphan,recovered_result
from competitor_intel.execution import execution_lock,AlreadyRunning

NOW=datetime(2026,10,13,10,tzinfo=timezone.utc)
def row():return {'id':30,'status':'RUNNING','finished_at':None,'result':{'pid':26676}}
def journal():return {'run_id':30,'pid':26676,'origin':'OPERATOR_UI','status':'RUNNING','sources':{'kuvalda_nnov':{'status':'PARTIAL','stored':44},'metalmaster':{'status':'COMPLETED','stored':3},'jet_online':{'status':'COMPLETED','stored':30}},'errors':[]}
COUNTS={'kuvalda_nnov':{'observations':44,'pages':44,'new_versions':44,'latest_observation':NOW},'metalmaster':{'observations':3,'pages':3,'new_versions':2,'latest_observation':NOW},'jet_online':{'observations':30,'pages':30,'new_versions':3,'latest_observation':NOW},'stanki_ru':{'observations':6,'pages':6,'new_versions':5,'latest_observation':NOW}}

class Cursor:
    def __init__(self,store):self.store=store;self.value=None
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def execute(self,sql,args=()):
        if sql.startswith('SELECT * FROM competitor_runs'):self.value=deepcopy(self.store.row)
        elif 'clock_timestamp' in sql:self.value={'recovered_at':NOW}
        elif sql.startswith('UPDATE competitor_runs'):
            assert args[3]==30 and self.store.row['status']=='RUNNING'
            self.store.updates+=1;self.store.row.update(status=args[0],finished_at=args[1],result=args[2].obj);self.value={'id':30}
        else:raise AssertionError('Unexpected SQL, especially facts mutation: '+sql)
    def fetchone(self):return self.value
class Store:
    def __init__(self):self.row=row();self.updates=0;self.conn=self
    def transaction(self):return nullcontext()
    def cursor(self):return Cursor(self)

def prepare(tmp_path):
    (tmp_path/'collection.lock').write_bytes(b'0');(tmp_path/'last-run.json').write_text(json.dumps(journal()))

def test_terminal_status_and_source_semantics():
    result=recovered_result(row(),journal(),COUNTS,NOW)
    assert result['status']=='PARTIAL' and result['exit_code']==1 and result['finished_at']==NOW.isoformat()
    assert result['sources']['stanki_ru']['status']=='PARTIAL' and result['sources']['stanki_ru']['execution_state']=='INTERRUPTED'
    assert result['sources']['titaniumgroup']['status']=='NOT_REACHED' and result['sources']['youmet']['status']=='NOT_REACHED'
    assert result['sources']['jet_online']['status']=='COMPLETED'
    assert result['recovery']['worker_exit_time'] is None and result['recovery']['new_collection'] is False

def test_recovery_only_run_metadata_preserves_lock_and_idempotent(tmp_path):
    prepare(tmp_path);s=Store();called=[];lock_before=(tmp_path/'collection.lock').stat().st_mtime_ns
    first=recover_orphan(s,tmp_path,30,snapshot=lambda *a:called.append(a),counts=COUNTS,alive=lambda pid:False)
    bytes_before={p.name:p.read_bytes() for p in tmp_path.iterdir()};times_before={p.name:p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    second=recover_orphan(s,tmp_path,30,snapshot=lambda *a:pytest.fail('No duplicate snapshot'),counts=COUNTS,alive=lambda pid:False)
    assert first['changed'] and not second['changed'] and s.updates==1 and len(called)==1
    assert first['result']==second['result'] and s.row['finished_at']==NOW
    assert bytes_before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    assert times_before=={p.name:p.stat().st_mtime_ns for p in tmp_path.iterdir()} and (tmp_path/'collection.lock').stat().st_mtime_ns==lock_before

def test_live_worker_refused_without_writes(tmp_path):
    prepare(tmp_path);s=Store()
    with pytest.raises(ValueError,match='WORKER_STILL_ALIVE'):recover_orphan(s,tmp_path,30,snapshot=lambda *a:pytest.fail('No snapshot'),counts=COUNTS,alive=lambda pid:True)
    assert s.updates==0 and json.loads((tmp_path/'last-run.json').read_text())['status']=='RUNNING'

def test_active_lock_stops_recovery(tmp_path):
    prepare(tmp_path);s=Store()
    with execution_lock(tmp_path):
        with pytest.raises(AlreadyRunning):recover_orphan(s,tmp_path,30,snapshot=lambda *a:pytest.fail('No snapshot'),counts=COUNTS,alive=lambda pid:False)
    assert s.updates==0

def test_other_run_journal_refused(tmp_path):
    prepare(tmp_path);j=journal();j['run_id']=31;(tmp_path/'last-run.json').write_text(json.dumps(j));s=Store()
    with pytest.raises(ValueError,match='JOURNAL_IDENTITY_MISMATCH'):recover_orphan(s,tmp_path,30,snapshot=lambda *a:None,counts=COUNTS,alive=lambda pid:False)
    assert s.updates==0

def test_partial_scope_does_not_create_removals_and_report_rebuild(tmp_path):
    from tests.test_competitor_monitoring_ui import fixture_state
    from competitor_intel.business_data import normalized_dataset
    from competitor_intel.business_report import export_preview
    state=fixture_state();state['competitor_ai_analysis']=[]
    state['competitor_runs'].append({'id':30,'status':'PARTIAL','result':{'recovery':{'coverage_complete':False}}})
    # One previously observed item is absent in an explicitly incomplete snapshot.
    state['competitor_campaign_items']=[i for i in state['competitor_campaign_items'] if i['page_version_id']!=2]
    result=export_preview(normalized_dataset(state),'2026-09-01','2026-10-12',tmp_path)
    events=json.loads((tmp_path/'EVENTS.json').read_text())
    assert not any(e['event_type'] in ('CAMPAIGN_PRODUCT_REMOVED','PRODUCT_DISAPPEARED_FROM_CURRENT_CAMPAIGN') for e in events)
    second=export_preview(normalized_dataset(state),'2026-09-01','2026-10-12',tmp_path)
    assert result['events']==second['events'] and second['new_events']==0 and second['duplicate_events']==0
