import json
from copy import deepcopy
import pytest
from universal_supplier.control_plane.source_resume import SourceCheckpoint,classify,pending_manifest

SOURCE='vekprom';SHA='a'*64;JOB=12
ORIGIN={'source':SOURCE,'job_id':JOB,'manifest_sha256':SHA}

def classify_test(entries,receipts,probe):return classify(entries,receipts,probe,source=SOURCE,parent_job_id=JOB,manifest_sha256=SHA)

def test_uncertain_and_missing_receipts_never_enter_http_pending():
    original={'format':'source-job-manifest/v1','supplier_code':SOURCE,'entries':[{'url':'https://vekprom.ru/'+str(i)} for i in range(4)]};before=deepcopy(original)
    receipts=[{'url':original['entries'][i]['url'],'state':s,'origin':ORIGIN} for i,s in enumerate(['COMMITTED','NOT_REQUESTED','UNCERTAIN'])]
    rows=classify_test(original['entries'],receipts,lambda e,r:'COMMITTED' if e['url'].endswith('/0') else 'NOT_FOUND')
    out=pending_manifest(original,rows,parent_job_id=JOB,parent_manifest_sha256=SHA)
    assert [e['url'] for e in out['http_pending']['entries']]==['https://vekprom.ru/1']
    assert out['uncertain']==['https://vekprom.ru/2','https://vekprom.ru/3'] and not out['automatic_resume_allowed']
    assert original==before and out['http_pending']['resume']['parent_manifest_sha256']==SHA

def test_sql_commit_receipt_wins_over_old_failed_state():
    e={'url':'https://vekprom.ru/1'};r={'url':e['url'],'origin':ORIGIN,'state':'FAILED'}
    assert classify_test([e],[r],lambda e,r:'COMMITTED')[0]['state']=='COMMITTED'

def test_missing_sql_proof_or_fake_capture_metadata_is_uncertain():
    e={'url':'https://vekprom.ru/1'}
    for state in ('COMMITTED','HTTP_CAPTURED_NOT_COMMITTED'):
        r={'url':e['url'],'state':state,'original_capture_metadata':True,'origin':ORIGIN}
        assert classify_test([e],[r],lambda e,r:'NOT_FOUND')[0]['state']=='UNCERTAIN'

def test_receipt_owner_is_bound_to_source_job_and_manifest():
    e={'url':'https://vekprom.ru/1'};r={'url':e['url'],'state':'COMMITTED','origin':{**ORIGIN,'job_id':13}}
    with pytest.raises(ValueError,match='OWNER'):classify_test([e],[r],lambda e,r:'COMMITTED')

def cp(tmp_path,**kw):
    def valid(url):
        if not url.startswith('https://vekprom.ru/'):raise ValueError('namespace')
    return SourceCheckpoint(tmp_path,SOURCE,JOB,SHA,validator=valid,lease_acquired=True,**kw)

def test_frontier_restart_does_not_blindly_reclaim_inflight(tmp_path):
    a=cp(tmp_path);a.add_urls(['https://vekprom.ru/a','https://vekprom.ru/b']);assert a.claim_batch(1)==['https://vekprom.ru/a']
    restarted=cp(tmp_path);assert restarted.state['inflight']['https://vekprom.ru/a']['state']=='UNCERTAIN'
    assert restarted.claim_batch(5)==['https://vekprom.ru/b']
    assert not restarted.complete

def test_committed_product_and_category_failure_block_full_completion(tmp_path):
    a=cp(tmp_path);a.add_urls(['https://vekprom.ru/a']);a.claim_batch(1)
    with pytest.raises(ValueError,match='COMMIT_PROOF'):a.record_terminal([{'url':'https://vekprom.ru/a','state':'COMMITTED'}])
    assert 'https://vekprom.ru/a' in a.state['inflight']
    a.record_terminal([{'url':'https://vekprom.ru/a','state':'COMMITTED','sql_verified':True}])
    a.finish_discovery(category_failures=['unread category']);assert not a.complete
    a.finish_discovery();assert a.complete

def test_discovery_frontier_children_visited_and_checksum(tmp_path):
    a=cp(tmp_path);a.add_urls(['https://vekprom.ru/catalog']);a.claim_batch(1)
    a.complete_discovery_node('https://vekprom.ru/catalog',['https://vekprom.ru/a','https://vekprom.ru/a'],evidence_sha256='b'*64)
    assert a.state['frontier']==['https://vekprom.ru/a'] and a.state['visited']==['https://vekprom.ru/catalog']
    assert cp(tmp_path).state==a.state
    data=json.loads(a.path.read_text(encoding='utf-8'));data['frontier'].append('tampered');a.path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='CHECKSUM'):cp(tmp_path)

def test_stale_writer_does_not_overwrite_and_failed_write_preserves_memory(tmp_path):
    a=cp(tmp_path);a.add_urls(['https://vekprom.ru/a']);stale=cp(tmp_path)
    a.add_urls(['https://vekprom.ru/b']);before=deepcopy(stale.state)
    with pytest.raises(RuntimeError,match='ANOTHER_WRITER'):stale.claim_batch(1)
    assert stale.state==before and cp(tmp_path).state==a.state

def test_foreign_url_validation_is_atomic(tmp_path):
    a=cp(tmp_path);before=deepcopy(a.state)
    with pytest.raises(ValueError,match='namespace'):a.add_urls(['https://vekprom.ru/a','https://foreign.example/a'])
    assert a.state==before and not a.path.exists()


def test_duplicate_classification_cannot_generate_duplicate_pending():
    e={'url':'https://vekprom.ru/1'};original={'supplier_code':SOURCE,'entries':[e]}
    rows=classify_test([e],[{'url':e['url'],'state':'NOT_REQUESTED','origin':ORIGIN}],lambda e,r:'NOT_FOUND')
    with pytest.raises(ValueError,match='DUPLICATE_CLASSIFICATION'):
        pending_manifest(original,rows+rows,parent_job_id=JOB,parent_manifest_sha256=SHA)


def test_late_failure_cannot_erase_verified_commit(tmp_path):
    a=cp(tmp_path);url='https://vekprom.ru/1';a.add_urls([url]);a.claim_batch(1)
    a.record_terminal([{'url':url,'state':'COMMITTED','sql_verified':True}]);before=deepcopy(a.state)
    with pytest.raises(ValueError,match='CANNOT_BE_DOWNGRADED'):a.record_terminal([{'url':url,'state':'FAILED'}])
    assert a.state==before and cp(tmp_path).state==before
