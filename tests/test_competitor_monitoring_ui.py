"""Explicit fixtures for read-service/native routes. No writes or source HTTP."""
import os
os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP']='YES'
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from competitor_intel.monitoring import MonitoringService,MonitoringUnavailable
from universal_supplier.control_plane.web import create_app
from universal_supplier.control_plane.store import UnavailableControlPlaneStore
from universal_supplier.control_plane.commercial_qa import DisabledCommercialQaService

NOW=datetime(2026,10,12,12,tzinfo=ZoneInfo('Europe/Moscow'))
def fixture_state():
    state={'competitor_sources':[{'id':1,'code':'fixture_auto','name':'AUTO FIXTURE','base_url':'https://fixture.example/','region':'Москва'}, {'id':2,'code':'youmet','name':'YouMet','base_url':'https://youmet.ru/','region':'Москва'}, {'id':3,'code':'fixture_error','name':'ERROR FIXTURE','base_url':'https://error.example/','region':None}],
      'competitor_pages':[],'competitor_page_versions':[],'competitor_campaign_items':[],'competitor_observations':[],
      'competitor_source_surfaces':[{'source_id':2,'canonical_url':'https://youmet.ru/','status':'ERROR','fetch_status':'HTTP_BLOCKED','reason':'ACTUAL_ACCESS_FAILURE','details':{}},{'source_id':3,'canonical_url':'https://error.example/','status':'ERROR','fetch_status':'ERROR','reason':'FIXTURE_FAILURE','details':{}}],
      'competitor_operator_captures':[],'competitor_runs':[{'result':{'coverage':[{'source':'fixture_auto','complete':True,'entities':['https://fixture.example/sale/'],'observed_at':'2026-10-11T10:00:00+03:00'}]}}]}
    for pid,url,kind,title,vids in [(1,'https://fixture.example/sale/','SALE_PAGE','Станки',[(1,'a','2026-10-10T10:00:00+03:00',100000),(2,'b','2026-10-12T10:00:00+03:00',86000)]),(2,'https://fixture.example/new-promo/','PROMOTION','Новая акция FIXTURE',[(3,'c','2026-10-12T10:01:00+03:00',50000)])]:
        state['competitor_pages'].append({'id':pid,'source_id':1,'canonical_url':url,'current_content_hash':vids[-1][1],'last_seen_at':vids[-1][2]})
        for vid,h,at,price in vids:
            state['competitor_page_versions'].append({'id':vid,'page_id':pid,'content_hash':h,'extractor_version':'fixture-v1','fetched_at':at,'qa_status':'ACCEPTED','payload':{'title':title,'page_type':kind,'published_at':'2026-10-12','valid_to':'2026-10-31','normalized_text':title,'item_scope_complete':False}})
            state['competitor_observations'].append({'id':vid,'page_id':pid,'page_version_id':vid,'observed_at':at,'transport':'HTTP'})
            state['competitor_campaign_items'].append({'id':vid,'page_version_id':vid,'product_name':'Очень длинное название токарного станка FIXTURE '+('слово '*18),'product_url':url+'product/','new_price':str(price),'old_price':None,'currency':'RUB','region':'Москва','brand':'BRAND','model':'A','category':'Станки','priority':'HIGH','price_type':'PUBLIC'})
    return state

class Repo:
    def __init__(self,data=None,fail=False):self.data=data or fixture_state();self.fail=fail;self.calls=0
    def load(self):
        self.calls+=1
        if self.fail:raise MonitoringUnavailable()
        return deepcopy(self.data)
class ReadOnlyStore(UnavailableControlPlaneStore):read_only=True
def client(service):return TestClient(create_app(store=ReadOnlyStore('fixture only'),auth_required=False,commercial_qa_service=DisabledCommercialQaService(),competitor_service=service))
def service(data=None,fail=False):return MonitoringService(Repo(data,fail),clock=lambda:NOW)

def test_native_tab_navigation_and_no_iframe():
    with client(service()) as c:
        assert 'Мониторинг конкурентов' in c.get('/').text
        text=c.get('/competitor-monitoring').text
        assert '<iframe' not in text and 'AUTO FIXTURE' in text and 'Цена снизилась' in text
        for tab in ('overview','changes','prices','promotions','assortment','sources'):assert c.get('/competitor-monitoring/'+tab).status_code==200

def test_real_event_engine_price_decrease_and_new_promotion():
    v=service().view('changes',{})
    drop=next(e for e in v['rows'] if e['event_type']=='PRICE_DECREASE')
    assert str(drop['percent_delta'])=='-14.00' and drop['severity']=='HIGH'
    assert any(e['event_type']=='NEW_PROMOTION' for e in v['rows'])

def test_manual_not_error_and_auto_page_warning_not_source_error():
    data=fixture_state();data['competitor_source_surfaces'].append({'source_id':1,'canonical_url':'https://fixture.example/missing/','status':'ERROR','fetch_status':'ERROR','reason':'ACTUAL_ACCESS_FAILURE','details':{}})
    v=service(data).view('sources',{});by={s['code']:s for s in v['sources']}
    assert by['youmet']['status']=='MANUAL' and not by['youmet']['baseline_confirmed'] and by['youmet']['last_capture'] is None
    assert by['fixture_auto']['status']=='WORKING' and len(by['fixture_auto']['issues'])==1
    assert by['fixture_error']['status']=='ERROR'

def test_no_manual_fixture_in_business_metrics():
    v=service().view('overview',{});manual=next(s for s in v['sources'] if s['code']=='youmet')
    assert manual['products']==0 and manual['pages']==0
    with client(service()) as c:
        t=c.get('/competitor-monitoring/sources?source=youmet').text
        assert 'Не предоставлен' in t and 'Не подтверждён' in t and 'HTTP 403' in t

def test_filters_api_and_bounded_pagination():
    with client(service()) as c:
        r=c.get('/api/competitor-monitoring/changes?type=down&brand=BRAND&search=токарного').json()
        assert r['total']==1 and len(r['rows'])==1 and r['rows'][0]['event_type']=='PRICE_DECREASE'
        assert c.get('/api/competitor-monitoring/changes?source=youmet').json()['total']==0
        assert c.get('/api/competitor-monitoring/changes?period=custom&from=2026-10-10&to=2026-10-12').status_code==200
        assert c.get('/api/competitor-monitoring/changes?period=custom&from=bad&to=bad').status_code==400

def test_db_unavailable_and_post_refused():
    with client(service(fail=True)) as c:
        r=c.get('/competitor-monitoring');assert r.status_code==200 and 'База мониторинга сейчас недоступна' in r.text and 'Traceback' not in r.text
        assert c.get('/api/competitor-monitoring/changes').status_code==503
        assert c.post('/competitor-monitoring').status_code in (403,405)

def test_empty_and_corrupt_source_isolation():
    data=fixture_state();data['competitor_page_versions'][-1]['payload']['valid_to']='bad date'
    v=service(data).view('sources',{});assert next(s for s in v['sources'] if s['code']=='fixture_auto')['status']=='ERROR'
    assert next(s for s in v['sources'] if s['code']=='youmet')['status']=='MANUAL'
    with client(service()) as c:assert 'Изменений за выбранный период нет' in c.get('/competitor-monitoring/changes?source=youmet').text

def test_cache_prevents_n_plus_one_reads():
    repo=Repo();s=MonitoringService(repo,clock=lambda:NOW)
    for tab in ('overview','changes','sources'):s.view(tab,{})
    assert repo.calls==1

def test_news_commercial_context_not_mixed_into_promotion_screen():
    data=fixture_state();data['competitor_page_versions'][-1]['payload'].update(page_type='NEWS',campaign_evidence={'current_campaign_in_news':True})
    assert service(data).view('promotions',{})['total']==0
    assert any(n['title']=='Новая акция FIXTURE' for n in service(data).view('overview',{})['news'])

def test_local_review_other_tabs_safe_and_writes_blocked():
    from scripts.run_competitor_monitoring_ui import build_app
    with TestClient(build_app()) as c:
        assert 'не подключается' in c.get('/products').text
        assert c.post('/commercial-qa/runs').status_code==403
