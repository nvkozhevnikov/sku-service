"""Actual copied DB + shared authentication; no live collection entry points."""
import json,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from rc_prepare_database import OUT,connect,TARGET
from rc_runtime import build_app,credentials,dispatch_connect
from universal_supplier.control_plane.unified_jobs import FrozenDispatch
from fastapi.testclient import TestClient

def main():
    checks={};app=build_app();dispatch=FrozenDispatch(dispatch_connect)
    with connect(TARGET) as c:
        preserved=c.execute("SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) FROM source_products t").fetchone()[0]
    with TestClient(app) as client:
        checks['supplier_and_competitor_need_login']=all(client.get(p,follow_redirects=False).status_code==303 for p in ('/products','/competitor-monitoring'))
        response=client.post('/login',data={'username':'rc-review','password':credentials()['qa_login_password']},headers={'origin':'http://testserver'},follow_redirects=False)
        checks['single_login']=response.status_code==303
        for path in ('/','/products','/suppliers','/competitor-monitoring','/competitor-monitoring/news','/competitor-monitoring/prices','/competitor-monitoring/changes','/release-jobs'):
            response=client.get(path)
            checks[path]=response.status_code==200
            if path.startswith('/competitor-monitoring'):
                checks[path+'_real_data']=('База мониторинга сейчас недоступна' not in response.text and 'Данные источника требуют проверки' not in response.text)
        import re
        token=re.search(r'name="csrf_token" value="([^"]+)"',client.get('/release-jobs').text).group(1)
        r=client.post('/release-jobs/enqueue',data={'csrf_token':token,'source_key':'supplier:ltt_msk'},headers={'origin':'http://testserver'})
        checks['manual_enqueue_202']=r.status_code==202
        duplicate=client.post('/release-jobs/enqueue',data={'csrf_token':token,'source_key':'supplier:ltt_msk'},headers={'origin':'http://testserver'})
        checks['duplicate_409']=duplicate.status_code==409
        checks['bad_csrf_403']=client.post('/release-jobs/enqueue',data={'csrf_token':'bad','source_key':'supplier:optimum'},headers={'origin':'http://testserver'}).status_code==403
        checks['cross_origin_403']=client.post('/release-jobs/enqueue',data={'csrf_token':token,'source_key':'supplier:optimum'},headers={'origin':'https://untrusted.invalid'}).status_code==403
        checks['normal_live_start_blocked']=client.post('/suppliers/1/start',data={'csrf_token':token},headers={'origin':'http://testserver'}).status_code==403
        first=dispatch.work_one();checks['manual_replay_369']=first['rows']==369 and first['external_http']==0
        with connect(TARGET) as c:
            prior=c.execute('SELECT max(due_slot) FROM rc_replay_jobs').fetchone()[0]
        due=prior+timedelta(days=1) if prior else datetime(2026,10,19,8,0,tzinfo=timezone.utc)
        checks['schedule_disabled']=dispatch.evaluate_due(due)==[]
        scheduled=dispatch.evaluate_due(due,simulate_enabled=True)
        checks['all_23_sources_declared']=len(scheduled)==23
        checks['missing_vekprom_blocked']=sum(r['status']=='BLOCKED_MISSING_VERIFIED_SOURCE_DATA' for r in scheduled)==1
        twice=dispatch.evaluate_due(due,simulate_enabled=True)
        checks['scheduled_slot_dedup']=all(r['status']!='queued' for r in twice)
        completed=[]
        while (r:=dispatch.work_one()) is not None:completed.append(r)
        checks['scheduled_22_replays_completed']=len(completed)==22 and all(r['external_http']==0 for r in completed)
        client.post('/logout',data={'csrf_token':token},headers={'origin':'http://testserver'})
        checks['shared_logout']=client.get('/competitor-monitoring',follow_redirects=False).status_code==303
    with connect(TARGET) as c:
        after=c.execute("SELECT md5(coalesce(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) FROM source_products t").fetchone()[0]
    checks['source_rows_unchanged']=after==preserved
    receipt={'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'external_http':0,'mock_or_replay_only':True,'live_scheduler_enabled':False}
    (OUT/'PANEL_CRON_REPLAY_QA.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2));assert all(checks.values())
if __name__=='__main__':main()
