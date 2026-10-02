from dataclasses import replace
import pytest
from scripts.run_beka_gap_ledger import parse_confirmed
from scripts import run_beka_gap_ledger as runner
from universal_supplier.commercial_persistence import HttpCapture
from datetime import datetime,timezone

def test_enclosure_needs_explicit_own_title(monkeypatch):
    from types import SimpleNamespace
    p=SimpleNamespace(site_internal_id='1',supplier_model='BMSO 570 XS CNC ENCLOSED',name='Ленточнопильный станок BMSO-570XS CNC с кабиной')
    monkeypatch.setattr(runner,'parse_bekamak_detail',lambda *a,**k:p)
    assert parse_confirmed('beka_mak','https://beka-mak.su/product/bmso_570xs_cnc_enclosed/','') is p
    p.name='Ленточнопильный станок BMSO-570XS CNC'
    with pytest.raises(ValueError): parse_confirmed('beka_mak','https://beka-mak.su/product/bmso_570xs/','')

def test_archive_detail_hash_basis_is_honest_and_hashes_agree():
    args=dict(requested_url='https://beka-mak.su/product/bmsy_270dg/',final_url='https://beka-mak.su/product/bmsy_270dg/',
        http_status=200,content_type='text/html',observed_at=datetime.now(timezone.utc),response_sha256='a'*64,
        evidence_sha256='a'*64,evidence_ref='saved.html',capture_scope='detail',response_hash_basis='sanitized_archive')
    capture=HttpCapture(**args)
    assert capture.response_hash_basis=='sanitized_archive'
    with pytest.raises(ValueError): HttpCapture(**{**args,'evidence_sha256':'b'*64})

def test_opaque_slug_does_not_prove_model(monkeypatch):
    from types import SimpleNamespace
    p=SimpleNamespace(site_internal_id='x',supplier_model='BMSO-330CS NC',name='BMSO-330CS NC')
    monkeypatch.setattr(runner,'parse_bekamak_official_detail',lambda *a,**k:p)
    assert parse_confirmed('beka_mak_tr','https://www.bekamak.com/urun/bmso-330cs-nc_DWXGR/en','') is p
    with pytest.raises(ValueError): parse_confirmed('beka_mak_tr','https://www.bekamak.com/urun/bmso-330cs/en','')
