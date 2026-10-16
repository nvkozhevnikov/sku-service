"""Explicit provenance gates plus the newly authorized separate source card."""
import copy
import pytest
from scripts import ltt_uss_replay_seals as s
from scripts.prepare_product_kind_29 import P
from universal_supplier.adapters.ltt_uss_html import source_evidence

def test_msk318_separate_identity_authorized_saw_blade():
    rows=s.r.load(P/'AUTHORIZED_29_ROWS.json')['rows']
    a=next(x for x in rows if x['supplier']=='ltt_msk' and x['index']==318)
    b=next(x for x in rows if x['supplier']=='ltt_msk' and x['index']==40)
    assert a['external_id']=='1642' and b['external_id']=='1860'
    assert a['db_evidence']['source_product_id']!=b['db_evidence']['source_product_id']
    kind=source_evidence(a['title'],a['raw_enrichment_evidence']['source_category'],(),'')['product_kind']
    assert kind['product_kind']=='saw_blade' and kind['confidence']=='HIGH'

def test_row129_domain_and_existing_sql_availability_agree():
    from universal_supplier.commercial_persistence import normalize_availability
    from scripts.prepare_product_kind_29 import o
    manifest=s.r.load(P/'PRODUCT_KIND_29_MANIFEST.json')['rows']
    entry=next(a for a in manifest if a['supplier']=='ltt_msk' and a['index']==129)
    record=s.r.load(o.ROOT/entry['original_db_record'])
    assert record['parsed_product']['availability']=='OutOfStock'
    assert record['offer']['availability_normalized']==normalize_availability('OutOfStock')=='out_of_stock'

def test_explicit_pre_product_kind_archive_and_terminal_link():
    before=s.named_seal(s.PRE_KIND);terminal=s.named_seal(s.TERMINAL)
    assert {k:v for k,v in before.items() if k in terminal}==terminal
    assert len(before)==12 and len(terminal)==11
    assert s.verify_active_code(baseline_seal=s.PRE_KIND,working_hashes=before)['working_code_hashes']==before

def test_current_proposal_requires_explicit_transition():
    # This proposal is now immutable history, selected from archived bytes.
    selected=s.verify_active_code(baseline_seal=s.PRE_KIND,proposed_transition=s.KIND_PROPOSAL,
        working_hashes=s.named_seal(s.KIND_PROPOSAL))
    assert selected['working_code_hashes']==s.named_seal(s.KIND_PROPOSAL)
    with pytest.raises(AssertionError,match='WORKING_CODE_MISMATCH'):
        s.verify_active_code(baseline_seal=s.PRE_KIND)

def test_current_wrong_code_fails_closed():
    actual=copy.deepcopy(s.named_seal(s.KIND_PROPOSAL));actual['sterbrust_matching/product_identity.py']='0'*64
    with pytest.raises(AssertionError,match='WORKING_CODE_MISMATCH'):
        s.verify_active_code(baseline_seal=s.PRE_KIND,proposed_transition=s.KIND_PROPOSAL,working_hashes=actual)

def test_missing_checkpoint_has_no_working_file_fallback(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'CHECKPOINT_PROVENANCE',tmp_path/'missing.json')
    with pytest.raises(AssertionError,match='PROVENANCE_MISSING'):s.named_seal(s.PRE_KIND)

def test_requested_seal_missing_fails_closed(monkeypatch):
    real=s.r.load;manifest=copy.deepcopy(real(s.CHECKPOINT_PROVENANCE));manifest['archives'].pop(s.PRE_KIND)
    monkeypatch.setattr(s.r,'load',lambda p:manifest if p==s.CHECKPOINT_PROVENANCE else real(p))
    with pytest.raises(AssertionError,match='NAMED_SEAL_MISSING'):s.named_seal(s.PRE_KIND)

def test_archived_historical_bytes_mismatch_fails_closed(monkeypatch):
    real=s.r.load;manifest=copy.deepcopy(real(s.CHECKPOINT_PROVENANCE))
    manifest['archives'][s.ORIGINAL][next(iter(manifest['archives'][s.ORIGINAL]))]['sha256']='0'*64
    monkeypatch.setattr(s.r,'load',lambda p:manifest if p==s.CHECKPOINT_PROVENANCE else real(p))
    with pytest.raises(AssertionError,match='ARCHIVED_CODE_REWRITTEN'):s.named_seal(s.ORIGINAL)

def test_rewritten_checkpoint_provenance_fails_closed(tmp_path,monkeypatch):
    p=tmp_path/'changed.json';p.write_text('{}',encoding='utf8')
    monkeypatch.setattr(s,'CHECKPOINT_PROVENANCE',p)
    with pytest.raises(AssertionError,match='PROVENANCE_REWRITTEN'):s.named_seal(s.PRE_KIND)

@pytest.mark.parametrize('name',[None,'','LATEST','unknown'])
def test_no_implicit_named_seal(name):
    with pytest.raises(ValueError,match='EXPLICIT_APPROVED'):s.named_seal(name)
