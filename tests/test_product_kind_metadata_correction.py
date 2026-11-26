"""No SQL server: exact allowlist, full-state CAS and transaction rollback."""
import copy
import json
from contextlib import contextmanager
import pytest
from scripts import product_kind_metadata_correction as m
from scripts.prepare_product_kind_29 import P,r,o

def manifest():return r.load(P/'PRODUCT_KIND_29_MANIFEST.json')

def test_metadata_patch_preserves_every_other_key():
    old={'id':1,'price':'42000','raw_data':{'commercial_capture':{'hash':'original'},'other':42,
        'enrichment_evidence':{'model_raw':'PB','product_kind':{'product_kind':'machine'}}}}
    new=m.expected_after(old,{'product_kind':'fixture'})
    assert old['raw_data']['enrichment_evidence']['product_kind']=={'product_kind':'machine'}
    restored=copy.deepcopy(new);restored['raw_data']['enrichment_evidence']['product_kind']={'product_kind':'machine'}
    assert restored==old

def test_half_corrected_pair_and_commercial_drift_refused():
    row={'id':1,'price':'42000','raw_data':{'enrichment_evidence':{'product_kind':{'product_kind':'machine'}}}}
    before=(row,copy.deepcopy(row));new={'product_kind':'fixture'}
    with pytest.raises(AssertionError,match='PARTIAL_PAIR'):
        m.compare_pair(before,(m.expected_after(row,new),row),new)
    mutated=copy.deepcopy(row);mutated['price']='0'
    with pytest.raises(AssertionError,match='BEFORE_STATE'):
        m.compare_pair(before,(row,mutated),new)

class Result:
    def __init__(self,rows):self.rows=rows
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows

class Fake:
    def __init__(self,data,rollback,fail_update=None):
        self.data=copy.deepcopy(data);self.rollback=rollback;self.updates=0;self.fail_update=fail_update;self.target=('universal_supplier_ltt_uss','127.0.0.1',55455,'17.11')
    @contextmanager
    def transaction(self):
        before=copy.deepcopy(self.data)
        try:yield
        except BaseException:self.data=before;raise
    def execute(self,query,args=None):
        if query.startswith('SELECT current_database'):return Result([self.target])
        if query.lstrip().startswith('SELECT row_to_json'):
            p=self.data['source_products',args[2]];v=self.data['offers',args[3]]
            return Result([(copy.deepcopy(p),copy.deepcopy(v))])
        assert query.startswith('UPDATE ')
        assert self.rollback.exists(),'Rollback evidence must precede all writes'
        self.updates+=1
        if self.updates==self.fail_update:return Result([])
        table=query.split()[1];kind,key,old=args;assert json.loads(old)==self.data[table,key]
        self.data[table,key]=m.expected_after(self.data[table,key],json.loads(kind))
        return Result([(copy.deepcopy(self.data[table,key]),)])

@pytest.fixture
def operation(tmp_path,monkeypatch):
    mf=manifest();data={}
    for a in mf['rows']:
        record=r.load(o.ROOT/a['original_db_record'])
        data['source_products',a['source_product_id']]=record['product']
        data['offers',a['offer_id']]=record['offer']
    monkeypatch.setattr(m,'validate_gate',lambda:None)
    monkeypatch.setattr(m.r,'verify_system',lambda:dict(version='17.11',system_identifier='7696153366151515704'))
    rollback=tmp_path/'rollback.json';return mf,Fake(data,rollback),rollback

def test_29_pair_transaction_and_second_execution_zero(operation):
    mf,c,path=operation
    assert m.apply(c,mf,rollback_path=path)['changed_rows']==29 and c.updates==58
    saved=path.read_bytes();data=copy.deepcopy(c.data)
    assert m.apply(c,mf,rollback_path=path,verify_only=True)['changed_rows']==0
    assert c.data==data and path.read_bytes()==saved and c.updates==58

def test_mid_transaction_failure_rolls_back_all29(operation):
    mf,c,path=operation;before=copy.deepcopy(c.data);c.fail_update=5
    with pytest.raises(AssertionError,match='COMPARE_AND_SET'):m.apply(c,mf,rollback_path=path)
    assert c.data==before and path.exists()

@pytest.mark.parametrize('target',[
    ('universal_supplier_ltt_uss','127.0.0.1',55452,'17.11'),
    ('universal_supplier_ltt_uss','127.0.0.2',55455,'17.11'),
    ('universal_supplier_ltt_uss','127.0.0.1',55455,'17.10'),
])
def test_wrong_isolated_port_refused_before_write(operation,target):
    mf,c,path=operation;c.target=target
    with pytest.raises(AssertionError,match='ISOLATED_TARGET'):m.apply(c,mf,rollback_path=path)
    assert c.updates==0 and not path.exists()

def test_wrong_system_identifier_refused_before_write(operation,monkeypatch):
    mf,c,path=operation
    monkeypatch.setattr(m.r,'verify_system',lambda:dict(version='17.11',system_identifier='wrong'))
    with pytest.raises(AssertionError,match='ISOLATED_SYSTEM'):m.apply(c,mf,rollback_path=path)
    assert c.updates==0 and not path.exists()

def test_partial_allowlist_correction_refused_before_write(operation):
    mf,c,path=operation;a=mf['rows'][0]
    for table,key in [('source_products',a['source_product_id']),('offers',a['offer_id'])]:c.data[table,key]=m.expected_after(c.data[table,key],a['new_kind'])
    with pytest.raises(AssertionError,match='PARTIALLY_CORRECTED_ALLOWLIST'):m.apply(c,mf,rollback_path=path)
    assert c.updates==0 and not path.exists()

def test_foreign_or_operator_row_cannot_enter_allowlist():
    bad=copy.deepcopy(manifest());bad['rows'][0]['index']=941
    with pytest.raises(AssertionError,match='ALLOWLIST'):m.validate_manifest(bad)

def test_verification_mode_never_corrects_old_rows(operation):
    mf,c,path=operation
    with pytest.raises(AssertionError,match='UNCORRECTED_ROWS'):m.apply(c,mf,rollback_path=path,verify_only=True)
    assert c.updates==0 and not path.exists()
