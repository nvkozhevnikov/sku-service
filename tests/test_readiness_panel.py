import json
from contextlib import nullcontext
from fastapi.testclient import TestClient
from universal_supplier.control_plane.presentation import phase_summary,price_label,mark_quality_repeats
from universal_supplier.control_plane.web import create_app
from universal_supplier.control_plane.store import PostgresControlPlaneStore,UnavailableControlPlaneStore


def test_http_success_never_implies_sql_success_or_missing_zero():
    r={'status':'failed','fetched_count':0,'metadata':{'failure':'SQL CHECK failed','failed_stage':'sql_ingest_seconds','http_phase':{'successful_cards':50,'cards_per_minute':28.416,'request_metrics':{'requests_total':51,'http_2xx':51}}}}
    s=phase_summary(r)
    assert s['parsed']==50 and s['sql_count'] is None and s['sql_state']=='Ошибка SQL'
    assert s['matching'] is None and s['matching_status']=='не запускался'
    assert s['http_requested']==51 and s['http_successful']==51
    assert phase_summary({})['http_speed'] is None


def test_committed_ingest_zero_is_a_real_zero():
    assert phase_summary({'fetched_count':0,'metadata':{'default_offer_counts':{}}})['sql_count']==0


def test_price_states_zero_and_missing():
    assert price_label({'price':0,'currency':'RUB'})=='0 RUB'
    assert price_label({'price':0,'price_state':'price_on_request'})=='По запросу'
    assert price_label({'price':None})=='Цена не указана'


def test_quality_badge_only_for_equal_semantic_profile_not_every_same_run():
    rows=[{'id':6,'supplier_id':1,'crawl_run_id':12,'metrics':{'a':1}},{'id':5,'supplier_id':1,'crawl_run_id':12,'metrics':{'a':1}},{'id':7,'supplier_id':1,'crawl_run_id':12,'metrics':{'a':2}}]
    out=mark_quality_repeats(rows)
    assert [x['duplicate_profile'] for x in out]==[True,False,False]
    assert 'duplicate_profile' not in rows[0]


def test_real_run_route_separates_all_phases_and_escapes_sql_error():
    class Store(UnavailableControlPlaneStore):
        def run(self,id):return {'id':id,'supplier_name':'Partner','status':'failed','fetched_count':0,'errors':[],'metadata':{'failure':'<SQL failed>','http_phase':{'successful_cards':50},'failed_stage':'sql_ingest_seconds'}}
    c=TestClient(create_app(store=Store('offline'),auth_required=False));r=c.get('/runs/12')
    assert r.status_code==200 and 'PostgreSQL сохранено' in r.text and 'Ошибка SQL' in r.text
    assert 'не запускался' in r.text and 'Ошибок нет.' not in r.text
    assert '&lt;SQL failed&gt;' in r.text and '<SQL failed>' not in r.text


def test_history_covers_offer_events_and_separates_timestamps_for_partner():
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,q,args):self.q=q
        def fetchall(self):
            if 'entity_change_history' in self.q:
                assert 'LEFT JOIN offers' in self.q
                return [{'changed_at':'2026-10-20','field_group':'commercial','old_value':{},'new_value':{},'crawl_run_id':12}]
            return [{'observed_at':'2026-10-19','price_state':'price_on_request','price':None,'currency':'RUB','availability_normalized':'unknown','capture_id':1,'supplier_code':'partner_st'}]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def cursor(self):return Cursor()
    s=object.__new__(PostgresControlPlaneStore);s._connect=lambda:Connection()
    rows=s.product_history('partner_st','296');observation=next(r for r in rows if r.get('capture_id'))
    assert observation['changed_at'] is None and observation['observed_at']=='2026-10-19'
