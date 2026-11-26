"""The actual saved LIVE stop response; no HTTP/SQL or identity overrides."""
import gzip
import hashlib
from pathlib import Path
import re

import pytest
from universal_supplier.adapters.bekamak_official import parse_bekamak_official_detail
from universal_supplier.commercial import PriceState

URL='https://www.bekamak.com/urun/613vt/en'


@pytest.fixture
def saved():
    raw=gzip.decompress((Path(__file__).parent/'fixtures/commercial/real/bekamak_613vt_live_stop.html.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest()=='9958e465b14a55293260ac7c93f960095e460471dad6c2c74a4492b604ec2abc'
    return raw.decode('utf-8')


def test_saved_slug_sku_uses_full_corroborated_reference_identity(saved):
    card=parse_bekamak_official_detail(saved,source_url=URL)
    assert card.name==card.supplier_model==card.manufacturer_article=='BMSY-613VT'
    assert card.site_internal_id=='613vt'
    assert card.price.state==PriceState.MISSING and card.availability is None
    assert 'manufacturer_reference_not_commercial_offer' in card.diagnostics
    assert 'official_slug_sku_full_model_corroborated' in card.diagnostics


@pytest.mark.parametrize('sku',['613DG','BMSO-613VT','613VT-230V','VT',''])
def test_different_sku_is_still_rejected(saved,sku):
    changed,n=re.subn(r'("sku"\s*:\s*")613vt(")',lambda m:m[1]+sku+m[2],saved)
    assert n==1
    with pytest.raises(ValueError,match='title, name and SKU disagree'):
        parse_bekamak_official_detail(changed,source_url=URL)


def test_different_primary_schema_model_still_rejected(saved):
    changed,n=re.subn(r'("name"\s*:\s*")BMSY-613VT(")',lambda m:m[1]+'BMSY-613DG'+m[2],saved,count=1)
    assert n==1
    with pytest.raises(ValueError,match='title, name and SKU disagree'):
        parse_bekamak_official_detail(changed,source_url=URL)


def test_slug_must_corroborate_short_sku(saved):
    changed=saved.replace('/urun/613vt/en','/urun/613dg/en')
    with pytest.raises(ValueError,match='title, name and SKU disagree'):
        parse_bekamak_official_detail(changed,source_url=URL.replace('613vt','613dg'))
