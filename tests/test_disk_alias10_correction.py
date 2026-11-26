"""Atomic10 CAS contract exercised against in-memory SQL fake only."""
import copy,json
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from scripts import disk_alias_metadata_correction as c

class Fake:
    def __init__(self,manifest,rollback):
        self.state={};self.manifest=manifest;self.rollback=rollback;self.writes=0;self.fail_write=None
        self.target=('universal_supplier_ltt_uss','127.0.0.1',55455,'17.11')
        for a in manifest['rows']:
            record=c.r.load(c.o.ROOT/a['original_db_record'])
            for table,key in [('source_products','product'),('offers','offer')]:self.state[table,record[key]['id']]=copy.deepcopy(record[key])
    @contextmanager
    def transaction(self):
        before=copy.deepcopy(self.state)
        try:yield
        except BaseException:self.state=before;raise
    def execute(self,query,params=None):
        if query.startswith('SELECT current_database'):return SimpleNamespace(fetchone=lambda:self.target)
        if query.startswith('SELECT row_to_json'):
            supplier,external,pid,oid=params
            return SimpleNamespace(fetchall=lambda:[(copy.deepcopy(self.state['source_products',pid]),copy.deepcopy(self.state['offers',oid]))])
        assert query.startswith('UPDATE ')
        assert self.rollback.exists(),'Rollback evidence must precede first mutation'
        table=query.split()[1];payload,pid,expected=params;expected=json.loads(expected);row=self.state[table,pid]
        assert c.shared.same(expected,row)
        self.writes+=1
        if self.writes==self.fail_write:raise RuntimeError('simulated pair CAS failure')
        row['raw_data']['enrichment_evidence']['product_kind']=json.loads(payload)
        return SimpleNamespace(fetchone=lambda:(copy.deepcopy(row),))

@pytest.fixture
def setup_case(tmp_path,monkeypatch):
    manifest=c.r.load(c.B/'DISK_ALIAS_10_MANIFEST.json');path=tmp_path/'rollback.json';fake=Fake(manifest,path)
    monkeypatch.setattr(c,'validate_gate',lambda:dict(status='PASS'))
    monkeypatch.setattr(c.r,'verify_system',lambda:dict(system_identifier='7696153366151515704'))
    return manifest,path,fake

def test_atomic10_only_nested_kind_and_idempotent_second_verification(setup_case):
    manifest,path,fake=setup_case;before=copy.deepcopy(fake.state)
    receipt=c.apply(fake,manifest,rollback_path=path)
    assert receipt['committed'] and receipt['source_rows_updated']==receipt['offer_rows_updated']==10 and fake.writes==20
    for a in manifest['rows']:
        for table,pid in [('source_products',a['source_product_id']),('offers',a['offer_id'])]:
            assert c.shared.same(fake.state[table,pid],c.shared.expected_after(before[table,pid],a['new_kind']))
    again=c.apply(fake,manifest,rollback_path=path,verify_only=True)
    assert again['changed_rows']==0 and again['verified_pairs']==10 and fake.writes==20

def test_failure_rolls_back_all_source_and_offer_pairs(setup_case):
    manifest,path,fake=setup_case;before=copy.deepcopy(fake.state);fake.fail_write=20
    with pytest.raises(RuntimeError,match='CAS failure'):c.apply(fake,manifest,rollback_path=path)
    assert fake.state==before and len(c.r.load(path)['rows'])==10

@pytest.mark.parametrize('change',[('other','127.0.0.1',55455,'17.11'),('universal_supplier_ltt_uss','127.0.0.1',55452,'17.11'),('universal_supplier_ltt_uss','127.0.0.2',55455,'17.11'),('universal_supplier_ltt_uss','127.0.0.1',55455,'17.10')])
def test_wrong_target_has_no_write(setup_case,change):
    manifest,path,fake=setup_case;fake.target=change
    with pytest.raises(AssertionError,match='ISOLATED_TARGET'):c.apply(fake,manifest,rollback_path=path)
    assert fake.writes==0 and not path.exists()

def test_scope_expansion_and_changed_id_refused(setup_case):
    manifest,path,fake=setup_case;changed=copy.deepcopy(manifest);changed['rows'].append(changed['rows'][0])
    with pytest.raises(AssertionError,match='EXACT_10'):c.apply(fake,changed,rollback_path=path)
    changed=copy.deepcopy(manifest);changed['rows'][0]['external_id']='wrong'
    with pytest.raises(AssertionError,match='IDENTITY'):c.apply(fake,changed,rollback_path=path)
    assert fake.writes==0

def test_before_state_drift_and_uncorrected_verify_refused(setup_case):
    manifest,path,fake=setup_case
    with pytest.raises(AssertionError,match='UNCORRECTED'):c.apply(fake,manifest,rollback_path=path,verify_only=True)
    key=next(iter(fake.state));fake.state[key]['name']='other'
    with pytest.raises(AssertionError,match='BEFORE_STATE'):c.apply(fake,manifest,rollback_path=path)
    assert fake.writes==0 and not path.exists()
