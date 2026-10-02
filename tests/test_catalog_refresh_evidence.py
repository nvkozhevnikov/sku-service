from copy import deepcopy
import pytest
from universal_supplier.catalog_refresh_evidence import commercial_evidence
from universal_supplier.rc_catalog_refresh import rc_availability
from universal_supplier.rc_catalog_refresh import apply_one

def row(state='price_on_request',price=''):
    return {'price_state':state,'price_amount':price,'alias_evidence':{'hidden_price':'0'},'identity_conflict':False}
def test_on_request_is_null_not_zero_and_input_unchanged():
    original=row();old=deepcopy(original);result=commercial_evidence(original)
    assert result['normalized_price'] is None and result['hidden_price_metadata']['value']=='0'
    assert original==old
def test_visible_price_primary_hidden_is_not_identity():
    original=row('numeric','1250');original.update(identity_conflict=True)
    result=commercial_evidence(original,hidden_price_only=True)
    assert result['normalized_price']=='1250' and not result['identity_conflict']
    assert result['commercial_metadata_conflict']
def test_unverified_conflict_never_cleared():
    original=row('numeric','1250');original['identity_conflict']=True
    assert commercial_evidence(original)['identity_conflict']
def test_availability_schema_bridge_keeps_raw_separate():
    assert rc_availability('on_request')=='preorder'
    assert rc_availability('in_stock')=='in_stock'
    with pytest.raises(ValueError):rc_availability('invented')
def test_rc_replay_exits_before_any_projection_or_observation_write():
    class Cursor:
        def __init__(self):self.sql=[];self.answer=None
        def execute(self,sql,args=()):
            self.sql.append(sql)
            if sql.startswith('SELECT id FROM suppliers'):self.answer=(1,)
            elif sql.startswith('SELECT id,raw_data'):self.answer=(42,{},'old','article','url','url',True,None)
            elif sql.startswith('SELECT id FROM supplier_http_captures'):self.answer=(99,)
        def fetchone(self):return self.answer
    cursor=Cursor();r={**row(),'supplier_code':'optimum','source_identity':'123'}
    result=apply_one(cursor,r,commit='fixture',observed_at='2026-10-02',evidence_ref='fixture://snapshot')
    assert result['exact_noop'] and result['source_product_id']==42
    assert not any(sql.startswith(('INSERT','UPDATE','DELETE')) for sql in cursor.sql)
def test_rc_writer_rejects_unapproved_namespace_and_identity_conflict_before_sql():
    for supplier,conflict in [('production',False),('optimum',True)]:
        with pytest.raises(ValueError):
            apply_one(None,{'supplier_code':supplier,'identity_conflict':conflict},commit='x',observed_at='x',evidence_ref='x')
@pytest.mark.parametrize('state,amount',[('price_on_request','0'),('missing','5'),('numeric',''),('numeric','NaN'),('numeric','-3')])
def test_bad_states_fail_closed(state,amount):
    with pytest.raises(ValueError):commercial_evidence(row(state,amount))
