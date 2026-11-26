"""Empty attempts never erase prior results or bypass strict freshness/identity."""
from copy import deepcopy
from datetime import timedelta
import json
import pytest
from tests.test_automation_system import snapshot
from universal_supplier.dual_preview import build,publish,latest,publication_status
from universal_supplier.export_continuity import compare
from xml.etree import ElementTree as ET

def test_explained_ttl_empty_keeps_last_good_and_archives_attempt(tmp_path):
    s=snapshot();data,good=build(s);publish(tmp_path,data,good)
    empty,m=build(s,as_of=s['as_of']+timedelta(days=100),baseline=good)
    assert m['counts']=={'updates':0,'new_products':0}
    publish(tmp_path,empty,m)
    assert latest(tmp_path,'updates')==data['updates']
    assert latest(tmp_path,'new_products')==data['new_products']
    assert latest(tmp_path,'updates',attempt=True)==empty['updates']
    status=publication_status(tmp_path)
    assert status['retained_previous'] and not status['import_allowed']
    assert status['gate']['status']=='PASS'
    assert len(status['gate']['explained_exclusions'])==2

def test_unexplained_loss_blocks_pointer_but_preserves_diagnostics(tmp_path):
    s=snapshot();d,m=build(s);publish(tmp_path,d,m)
    s['records']=[];empty,attempt=build(s)
    with pytest.raises(ValueError,match='NO_UNEXPLAINED'):publish(tmp_path,empty,attempt)
    assert latest(tmp_path,'updates')==d['updates']
    assert publication_status(tmp_path)['gate']['status']=='BLOCKED'
    with pytest.raises(ValueError,match='CONTINUITY_BLOCKED'):
        latest(tmp_path,'updates',attempt=True,require_continuity=True)

def test_missing_or_mismatched_publication_status_never_serves_attempt(tmp_path):
    d,m=build(snapshot());publish(tmp_path,d,m)
    status=publication_status(tmp_path);status['attempt']['version']='0'*64
    (tmp_path/'publication_status.json').write_text(json.dumps(status))
    with pytest.raises(ValueError,match='CONTINUITY_BLOCKED'):
        latest(tmp_path,'updates',attempt=True,require_continuity=True)
    assert latest(tmp_path,'updates')==d['updates']

def test_inventory_loss_or_canonical_remap_is_never_a_ttl_exception():
    inventory=dict(source_products=3,offers=3,commercial_observations=4,http_captures=4,suppliers=[[1,'partner_st']],canonical_links=[[1,11,'10001']])
    old={'data_inventory':inventory};new=deepcopy(old);new['data_inventory']['canonical_links']=[[1,12,'10002']]
    assert compare(old,new)['status']=='BLOCKED'
    new=deepcopy(old);new['data_inventory']['commercial_observations']=0
    assert compare(old,new)['status']=='BLOCKED'

def test_invalid_second_xml_never_advances_any_pointer(tmp_path):
    d,m=build(snapshot());publish(tmp_path,d,m)
    bad=dict(d,new_products=b'<broken')
    with pytest.raises(Exception):publish(tmp_path,bad,m)
    assert latest(tmp_path,'updates')==d['updates']

def test_lock_is_fail_closed_not_stolen(tmp_path):
    (tmp_path/'publish.lock').write_text('other publisher')
    with pytest.raises(ValueError,match='BUSY'):publish(tmp_path,*build(snapshot()))
    assert (tmp_path/'publish.lock').read_text()=='other publisher'

@pytest.mark.parametrize('missing,emitted,absent',[
    ('availability','price','availability'),('price','availability','price')])
def test_fields_are_independent_without_losing_confirmed_identity(missing,emitted,absent,tmp_path):
    s=snapshot();known=s['records'][0]
    if missing=='availability':known['observed_availability']='unknown'
    else:known['observed_price']=None
    d,m=build(s);publish(tmp_path,d,m)
    node=ET.fromstring(d['updates']).find('product')
    assert node.get('sterbrust_product_id')=='10001'
    assert node.find(emitted) is not None and node.find(absent) is None
    assert s['records'][0]['identity_confirmed'] is True

def test_equal_counts_cannot_hide_offer_identity_replacement():
    old={'data_inventory':dict(source_products=2,offers=2,commercial_observations=2,http_captures=2,
        suppliers=[[1,'partner_st']],canonical_links=[],offer_identities=[[1,11,1],[2,12,1]])}
    new=deepcopy(old);new['data_inventory']['offer_identities'][1]=[3,13,1]
    assert compare(old,new)['errors']==['DURABLE_IDENTITY_LOST:offer_identities']

def test_republishing_semantic_manifest_keeps_existing_bytes(tmp_path):
    d,m=build(snapshot());directory=publish(tmp_path,d,m)
    original=(directory/'manifest.json').read_bytes()
    publish(tmp_path,d,dict(reversed(list(m.items()))))
    assert (directory/'manifest.json').read_bytes()==original
    assert latest(tmp_path,'updates')==d['updates']

def test_damaged_preserved_file_blocks_new_publication(tmp_path):
    d,m=build(snapshot());directory=publish(tmp_path,d,m)
    (directory/'sterbrust_updates.preview.xml').write_bytes(b'broken')
    with pytest.raises(ValueError,match='CHECKSUM'):publish(tmp_path,d,m)

def test_retained_xml_does_not_freeze_inventory_at_first_snapshot(tmp_path):
    from universal_supplier.control_plane.supplier_automation import digest
    s=snapshot();d,m=build(s);publish(tmp_path,d,m)
    d,m=build(s,as_of=s['as_of']+timedelta(days=100))
    inventory=dict(source_products=2,offers=2,commercial_observations=2,http_captures=2,
        suppliers=[[1,'partner_st']],canonical_links=[])
    def attempt(n):
        current=deepcopy(m);current['data_inventory']={**inventory,'offers':n}
        current['version']=digest({k:v for k,v in current.items() if k!='version'})
        return current
    publish(tmp_path,d,attempt(2));publish(tmp_path,d,attempt(3))
    with pytest.raises(ValueError,match='NO_UNEXPLAINED'):publish(tmp_path,d,attempt(2))
    assert json.loads((tmp_path/'inventory_baseline.json').read_text())['offers']==3

def test_hash_mismatch_never_publishes_and_missing_stream_is_rejected(tmp_path):
    d,m=build(snapshot());publish(tmp_path,d,m)
    changed=dict(d,updates=d['updates'].replace(b'12000.50',b'99999.99'))
    with pytest.raises(ValueError,match='CHECKSUM'):publish(tmp_path,changed,m)
    with pytest.raises(ValueError,match='PAIR_REQUIRED'):publish(tmp_path,{'updates':d['updates']},m)
    assert latest(tmp_path,'updates')==d['updates']

def test_history_bytes_hash_and_download_boundaries(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from test_automation_admin import Store
    from universal_supplier.control_plane.web import create_app
    from universal_supplier.control_plane.admin_store import InMemoryAdminStore
    from universal_supplier.export_history import register,read
    monkeypatch.setenv('CONTROL_PLANE_EXPORT_ROOT',str(tmp_path))
    d,m=build(snapshot());publish(tmp_path,d,m)
    item=register(tmp_path,d['new_products'],label='Historical fixture',source='offline-fixture')
    (tmp_path/'historical_new_candidates.json').write_text(json.dumps({'historical_at':m['generated_at'],
        'members':[{'candidate_id':'old','source_product_id':102,'import_allowed':False}]}))
    client=TestClient(create_app(store=Store(),auth_required=False))
    page=client.get('/exports/previews');assert page.status_code==200 and item['id'] in page.text
    response=client.get('/exports/previews/history/'+item['id'])
    assert response.content==d['new_products'] and response.headers['x-sterbrust-import-allowed']=='false'
    cohort=client.get('/exports/previews/candidates/download').json()
    assert len(cohort['members'])==1 and cohort['members'][0]['current']['selected']
    assert client.get('/exports/previews/diagnostics/download').json()['counts']==m['counts']
    secure=TestClient(create_app(store=Store(),admin_store=InMemoryAdminStore(),auth_required=True,
        session_secret='offline-history-auth-fixture-secret-32bytes'))
    for route in ('history/'+item['id'],'candidates/download','diagnostics/download','preserved/updates'):
        assert secure.get('/exports/previews/'+route,follow_redirects=False).status_code in (303,401,403)
    (tmp_path/'history'/(item['id']+'.xml')).write_bytes(b'tampered')
    with pytest.raises(ValueError,match='CHECKSUM'):read(tmp_path,item['id'])
    assert client.get('/exports/previews/history/'+item['id']).status_code==404
