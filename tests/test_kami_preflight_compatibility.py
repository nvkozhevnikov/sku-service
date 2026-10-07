"""Narrow migration/legacy guards tested with real parser fixtures/producer."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts.kami_offline_preflight_guards import (
    migration_integrity,commercial_proof,CompatibilityGuardError,LEGACY_CANARIES)
from universal_supplier.adapters.kami import KamiAdapter
from universal_supplier.models import FetchRecord
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.kami_persistence import prepare_observation


class MigrationCompatibilityTests(unittest.TestCase):
    canonical=b'-- original comment\nSELECT 1;\n'

    def test_exact_LF_pass(self):
        self.assertFalse(migration_integrity(self.canonical,self.canonical,hashlib.sha256(self.canonical).hexdigest())['line_endings_only'])

    def test_CRLF_only_pass(self):
        self.assertTrue(migration_integrity(self.canonical.replace(b'\n',b'\r\n'),self.canonical,hashlib.sha256(self.canonical).hexdigest())['line_endings_only'])

    def test_substantive_and_non_line_ending_changes_fail(self):
        for raw in (b'-- original comment\nSELECT 2;\n', self.canonical.replace(b'SELECT ',b'SELECT  '),
                    self.canonical.replace(b'SELECT ',b'SELECT\t'),self.canonical.replace(b'original',b'changed'),
                    self.canonical.rstrip(),b'\xef\xbb\xbf'+self.canonical,
                    self.canonical.replace(b'\n',b'\r')):
            with self.subTest(raw=raw),self.assertRaises(CompatibilityGuardError):
                migration_integrity(raw,self.canonical,hashlib.sha256(self.canonical).hexdigest())

    def test_wrong_Git_blob_fail(self):
        with self.assertRaises(CompatibilityGuardError):
            migration_integrity(self.canonical,b'SELECT 2;\n',hashlib.sha256(self.canonical).hexdigest())

    def test_wrong_DB_sha_fail(self):
        with self.assertRaises(CompatibilityGuardError):
            migration_integrity(self.canonical,self.canonical,'0'*64)


class LegacyCompatibilityTests(unittest.TestCase):
    def payload(self, fixture='ironmac_single_sale.html'):
        raw=(Path(__file__).parent/'fixtures/kami'/fixture).read_text(encoding='utf-8')
        url='https://www.stanki.ru/catalog/test/item/'
        card=KamiAdapter().parse_product(FetchRecord(url,url,200,(),1,0,raw))
        url=card.canonical_url
        capture=HttpCapture(url,url,200,'text/html',datetime(2026,10,5,tzinfo=timezone.utc),
                            hashlib.sha256(raw.encode()).hexdigest(),hashlib.sha256(raw.encode()).hexdigest(),'fixture://kami')
        card,obs=prepare_observation(card,capture)
        pk=LEGACY_CANARIES[card.external_id]
        previous={'source_product_id':pk,'capture_id':pk+100}
        source=deepcopy(card.raw_data);source.pop('_kami_commercial_fact_hash');source['_commercial_projection_region']=''
        offer={'id':pk+10,'supplier_id':5812,'source_product_id':pk,'external_offer_id':card.external_id,
               'offer_kind':'default','active':False,'price':str(card.price) if card.price is not None else None,
               'old_price':str(card.old_price) if card.old_price is not None else None,'quantity':None,
               'currency':card.currency,'availability_raw':card.availability_raw,
               'availability_normalized':card.availability_normalized,'price_type':'unknown','price_raw':None,
               'price_source':None,'raw_data':deepcopy(source)}
        offer['raw_data'].pop('_commercial_projection_region')
        observation=asdict(obs);observation.update(supplier_id=5812,source_product_id=pk,offer_id=offer['id'],
                       capture_id=previous['capture_id'],price_state=obs.price_state.value,
                       observed_at=obs.observed_at.isoformat(),unusable_price_reasons=list(obs.unusable_price_reasons))
        return [card,capture,previous,source,offer,[observation]]

    def proof(self,args,**kwargs):
        return commercial_proof(*args,snapshot_verified=kwargs.get('snapshot_verified',True),
                                capture_verified=kwargs.get('capture_verified',True))

    def test_all_four_real_fixture_canaries_full_proof_pass(self):
        fixtures=('ironmac_single_sale.html','spectr_group_from.html','spectr_variant_standard.html','spectr_variant_counterspindle.html')
        accepted=set()
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                args=self.payload(fixture);before=deepcopy(args)
                result=self.proof(args); accepted.add(result['external_id'])
                self.assertFalse(result['hash_written']); self.assertEqual(args,before)
                self.assertNotIn('_kami_commercial_fact_hash',args[3])
        self.assertEqual(accepted,set(LEGACY_CANARIES))

    def test_decimal_scale_semantics_pass(self):
        args=self.payload();args[4]['price']='7093124.0000';args[4]['old_price']=Decimal('9190015.0000')
        self.proof(args)

    def test_unknown_fifth_missing_hash_fail(self):
        args=self.payload(); raw=deepcopy(args[0].raw_data);raw['site_internal_id']='999999'
        args[0]=replace(args[0],external_id='site:999999',raw_data=raw)
        args[2]['source_product_id']=999999
        with self.assertRaisesRegex(CompatibilityGuardError,'allowlist'):
            self.proof(args)

    def test_all_commercial_drifts_fail(self):
        mutations=[('price',lambda a:a[4].update(price='1')),
                   ('old price',lambda a:a[4].update(old_price=None)),
                   ('currency',lambda a:a[4].update(currency='USD')),
                   ('POR role',lambda a:a[3].update(price_state='price_on_request')),
                   ('FROM_PRICE role',lambda a:a[3]['price_extraction'].update(price_basis='from_price')),
                   ('visible availability',lambda a:a[4].update(availability_raw='Под заказ')),
                   ('normalized availability',lambda a:a[4].update(availability_normalized='unknown')),
                   ('quantity',lambda a:a[4].update(quantity='2')),
                   ('quantity state',lambda a:a[3].update(quantity_state='OBSERVED')),
                   ('extraction source',lambda a:a[5][0].update(price_source='hidden JSON-LD')),
                   ('observation price',lambda a:a[5][0].update(price=Decimal('1'))),
                   ('offer raw extraction',lambda a:a[4]['raw_data']['price_extraction'].update(variant_or_group=True)),
                   ('observation role',lambda a:a[5][0].update(price_type='purchase')),
                   ('offer price role',lambda a:a[4].update(price_type='purchase'))]
        for label,mutate in mutations:
            with self.subTest(label=label):
                args=self.payload();mutate(args)
                with self.assertRaises(CompatibilityGuardError): self.proof(args)

    def test_FROM_PRICE_not_converted_to_exact(self):
        args=self.payload('spectr_group_from.html');args[5][0]['unusable_price_reasons']=[]
        with self.assertRaises(CompatibilityGuardError): self.proof(args)

    def test_capture_content_PK_identity_and_snapshot_proof_required(self):
        for mutation,kwargs in [(lambda a:a[3]['commercial_capture'].update(response_sha256='f'*64),{}),
                                (lambda a:a[3].update(_kami_content_fact_hash='f'*64),{}),
                                (lambda a:a[2].update(source_product_id=1),{}),
                                (lambda a:a[4].update(external_offer_id='site:other'),{}),
                                (lambda a:None,{'capture_verified':False}),
                                (lambda a:None,{'snapshot_verified':False})]:
            args=self.payload();mutation(args)
            with self.subTest(kwargs=kwargs),self.assertRaises(CompatibilityGuardError): self.proof(args,**kwargs)

    def test_non_null_hash_mismatch_never_legacy_accepted(self):
        args=self.payload();args[3]['_kami_commercial_fact_hash']='f'*64
        with self.assertRaisesRegex(CompatibilityGuardError,'non-null'):
            self.proof(args)

    def test_legacy_proof_DB_writes_zero(self):
        with patch('psycopg.connect') as connect:
            self.proof(self.payload())
            connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
