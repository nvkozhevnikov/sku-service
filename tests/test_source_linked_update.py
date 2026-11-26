"""Real writer SQL-contract tests with packaged parsers; no PostgreSQL connection.

Target rows are synthetic matching doubles, not proof of historical SQL links.
The cursor stores executed projection/ledger mutations and rolls back on errors.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
from threading import RLock
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from test_source_jobs import saved_case
from universal_supplier.control_plane.source_jobs import SOURCES, parse_existing
from universal_supplier.control_plane.source_backend import SourceBackend
from universal_supplier.commercial_persistence import HttpCapture, product_card_from_snapshot, observation_from_snapshot
from universal_supplier.kami_persistence import prepare_observation
from universal_supplier.postgres import PostgresRepository
from universal_supplier.source_update import content_card
from universal_supplier.matching_service import RegistryContext, _source


def fixture(code):
    from universal_supplier.ltt_uss_capture import sanitise_for_charset
    from universal_supplier.http_capture import sanitise_html
    body, url, charset, _ = saved_case(code)
    clean = sanitise_for_charset(body, charset) if code.startswith('ltt_') or code == 'uss_stanko' else sanitise_html(body)
    product = parse_existing(code, clean.decode(), url)
    capture = HttpCapture(url, url, 200, 'text/html; charset=utf-8', datetime(2026,10,21,tzinfo=timezone.utc),
                          'a'*64, 'b'*64, 'fixture://B0/offline')
    card, observation = prepare_observation(product, capture) if code == 'kami' else (
        product_card_from_snapshot(product, capture), observation_from_snapshot(product, capture))
    return content_card(card), observation, capture


def registry(card):
    s = _source(content_card(card))
    row = dict(sterbrust_product_id='CONFIRMED-1282', name=s.name, brand=s.brand,
               model=s.model, article=s.manufacturer_article, category=s.category,
               properties=s.properties, supplier_identifiers={})
    return RegistryContext({'CONFIRMED-1282': row}, {}, {}, {}, {})


class DB:
    def __init__(self, card, linked):
        self.card = card
        self.catalog = 1282 if linked else None
        self.raw = deepcopy(card.raw_data)
        self.at = datetime(2026,10,20,tzinfo=timezone.utc)
        self.active = True
        self.captures = {}; self.observations = []; self.history = []
        self.statements = []; self.new_products = 0; self.new_offers = 0
        self.run_exists = True; self.fail_observation = False
        self.existing = True; self.offer_exists = True; self.capture_rows = {}
        self.answer = None; self.rowcount = 1
        self.lock=RLock();self.url_collision=False;self.tamper_readback=False

    @contextmanager
    def transaction(self):
        with self.lock:
            before = deepcopy({k:v for k,v in vars(self).items() if k not in ('statements','lock')})
            try:yield
            except BaseException:
                for k,v in before.items():setattr(self,k,v)
                raise

    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def fetchone(self):return self.answer
    def fetchall(self):return self.answer
    def execute(self, query, args=()):
        q = ' '.join(query.split()); self.statements.append((q,args)); self.answer = None; self.rowcount = 1
        # Immutable mapping: even a successful test must never issue a binding write.
        if q.startswith('UPDATE source_products'):assert 'SET catalog_product_id' not in q and 'catalog_product_id=%s,' not in q
        if q.startswith('SELECT pg_advisory'):pass
        elif q.startswith('SELECT external_id,source_url,canonical_url'):self.answer = [(self.card.external_id,self.card.requested_url,self.card.canonical_url)] if self.card else []
        elif q.startswith('SELECT id, catalog_product_id'):self.answer = (7,self.catalog,self.at,self.raw) if self.existing else None
        elif q.startswith('SELECT r.id FROM crawl_runs'):self.answer = (12,) if self.run_exists else None
        elif q.startswith('SELECT id FROM source_products WHERE supplier_id'):
            self.answer=[(7,)] if self.existing or self.url_collision else []
        elif q.startswith('SELECT id,active FROM offers'):self.answer = (8,self.active)
        elif q.startswith('SELECT media_type,source_url'):
            self.answer=[(m.media_type,m.url,m.title,m.position,m.is_primary) for m in self.card.media]
        elif q.startswith('SELECT o.id,o.raw_name'):
            self.answer=[(i,o.name,o.position,v.value,v.target_url,v.variant_internal_id,v.classification,v.selected,v.position)
                         for i,o in enumerate(self.card.options) for v in o.values]
        elif q.startswith('SELECT id FROM supplier_http_captures'):
            self.answer = (self.captures[args[1]],) if args[1] in self.captures else None
        elif q.startswith('SELECT id FROM offers'):self.answer = (8,) if self.offer_exists else None
        elif q.startswith('UPDATE source_products SET sku'):
            self.card = replace(self.card,sku=args[0],name=args[1],requested_url=args[2],canonical_url=args[3])
            self.raw = json.loads(args[4]); self.at = datetime.fromisoformat(args[6])
        elif q.startswith('UPDATE source_products SET brand_raw'):
            assert args[-1] == self.catalog
            self.raw = json.loads(args[4])
            self.card = replace(self.card, brand=args[0] or '', manufacturer=args[1] or '',
                                description_text=args[2], description_html=args[3],raw_data=self.raw)
        elif q.startswith('UPDATE offers SET sku'):
            self.card = replace(self.card,sku=args[0],name=args[1],price=args[3] if args[2] else self.card.price,
                                old_price=args[5] if args[4] else self.card.old_price,
                                currency=args[7] if args[6] else self.card.currency,
                                availability_raw=args[9] if args[8] else self.card.availability_raw,
                                availability_normalized=args[11] if args[10] else self.card.availability_normalized, quantity=None)
            self.active = False
        elif q.startswith('UPDATE offers SET quantity'):self.card = replace(self.card,quantity=args[0]); self.active=args[1]
        elif q.startswith('UPDATE offers SET price_type'):
            self.card=replace(self.card,price_type=args[0],price_raw=args[1],price_source=args[2])
        elif q.startswith('INSERT INTO source_products'):
            self.new_products += 1; self.answer=(7,); self.raw=json.loads(args[6]); self.at=datetime.fromisoformat(args[7]);self.existing=True
        elif q.startswith('INSERT INTO offers'):self.new_offers+=1;self.answer=(8,);self.offer_exists=True;self.active=False
        elif q.startswith('INSERT INTO supplier_http_captures'):
            self.captures[args[7]]=len(self.captures)+1; self.answer=(self.captures[args[7]],)
            self.capture_rows[args[7]]=args
        elif q.startswith('INSERT INTO offer_commercial_observations'):
            if self.fail_observation:raise RuntimeError('INJECTED_BEFORE_LEDGER_COMMIT')
            self.observations.append(args);self.answer=(len(self.observations),)
        elif q.startswith('INSERT INTO entity_change_history'):self.history.append(args)
        elif q.startswith('SELECT catalog_product_id'):self.answer=(999 if self.tamper_readback else self.catalog,)
        elif q.startswith('SELECT c.evidence_sha256'):
            code,fp,url,external,external_optional=args;row=self.capture_rows.get(fp)
            self.answer=[] if not row or self.card.external_id != external or row[2]!=url else [
                (row[9],datetime.fromisoformat(row[6]),row[3],any(o[3]==self.captures[fp] for o in self.observations))]
        else:raise AssertionError(q)


def backend(monkeypatch, code, linked=True):
    card, obs, cap = fixture(code); db=DB(card,linked)
    repo=object.__new__(PostgresRepository);repo.connection=db
    repo._ensure_passive_commercial_supplier=lambda q,c: 6
    repo._replace_details=Mock(side_effect=lambda q,s,p,o,c,a:setattr(db,'card',c))
    repo._record_field_observations=Mock();repo._record_seen_observation=Mock()
    monkeypatch.setattr('universal_supplier.identity_service._load_cards',lambda *a,**kw:([replace(db.card,currency=db.card.currency or '')],{(code,db.card.external_id):7}))
    monkeypatch.setattr('universal_supplier.matching_service.load_registry_from_postgres',lambda *a,**kw:registry(card))
    return repo,db,card,obs,cap


def write(repo,card,obs,cap):return repo._persist_passive_observation(card,obs,cap,source_run_id=12)


@pytest.mark.parametrize('code',SOURCES)
def test_B0_reproducer_old_passive_writer_rejects_fresh_linked_capture(monkeypatch,code):
    repo,db,card,obs,cap=backend(monkeypatch,code)
    with pytest.raises(RuntimeError,match='refuses a catalog-linked'):
        repo._persist_passive_observation(card,obs,cap)
    assert not db.captures and not db.observations and not db.history


@pytest.mark.parametrize('code',SOURCES)
@pytest.mark.parametrize('linked',[False,True])
def test_existing_fixture_refresh_replay_and_real_commercial_change(monkeypatch,code,linked):
    repo,db,card,obs,cap=backend(monkeypatch,code,linked)
    before=db.catalog
    first=write(repo,card,obs,cap)
    assert first['source_product_id']==7 and first['offer_id']==8
    assert first['ingest_counts'].new_products==first['ingest_counts'].new_offers==0
    assert not db.history and db.catalog==before and db.active
    repeat=write(repo,card,obs,cap)
    assert repeat['exact_noop'] and len(db.captures)==len(db.observations)==1 and not db.history
    fresh=replace(cap,observed_at=cap.observed_at+timedelta(hours=1))
    same=write(repo,card,obs,fresh)
    assert not db.history and same['ingest_counts'].change_history_events==0
    assert len(db.captures)==len(db.observations)==2
    changed=replace(card,price=Decimal('123456.25'),old_price=None,currency='RUB',
                    availability_raw='Есть в наличии',availability_normalized='in_stock',quantity=Decimal('3'))
    obs=replace(obs,price_state=__import__('universal_supplier.commercial',fromlist=['PriceState']).PriceState.NUMERIC_PUBLIC,
                price=changed.price,old_price=None,currency='RUB',availability_raw=changed.availability_raw,availability_normalized='in_stock')
    cap2=replace(fresh,observed_at=fresh.observed_at+timedelta(hours=1),response_sha256='c'*64)
    result=write(repo,changed,obs,cap2)
    assert db.catalog==before and db.new_products==db.new_offers==0
    assert db.card.price==changed.price and db.card.quantity==Decimal('3') and db.card.availability_normalized=='in_stock'
    assert len(db.history)==1 and db.history[0][4]=='commercial'
    assert result['ingest_counts'].changed_offers==1
    assert len(db.captures)==len(db.observations)==3
    write(repo,changed,obs,replace(cap2,observed_at=cap2.observed_at+timedelta(hours=1)))
    assert len(db.history)==1


@pytest.mark.parametrize('code',SOURCES)
def test_semantic_conflict_rolls_back_without_rebinding(monkeypatch,code):
    repo,db,card,obs,cap=backend(monkeypatch,code)
    bad=replace(card,name='Патрон для постороннего станка OTHER-999',brand='OTHER',sku='OTHER',
                raw_data={**card.raw_data,'source_identity':{'raw_model':'OTHER-999','brand':'OTHER'}})
    with pytest.raises(RuntimeError,match='SEMANTIC_CONFLICT'):write(repo,bad,obs,cap)
    assert db.catalog==1282 and not db.captures and not db.history and db.new_products==db.new_offers==0


@pytest.mark.parametrize('key',['catalog_product_id','sterbrust_product_id'])
def test_explicit_rebinding_payload_conflicts_before_mutation(monkeypatch,key):
    repo,db,card,obs,cap=backend(monkeypatch,'kami')
    with pytest.raises(RuntimeError,match='TARGET_CHANGE_CONFLICT'):
        write(repo,replace(card,raw_data={**card.raw_data,'attempt':{key:'DIFFERENT'}}),obs,cap)
    assert db.catalog==1282 and not db.captures


def test_atomic_failure_does_not_leave_projection_history_or_capture(monkeypatch):
    repo,db,card,obs,cap=backend(monkeypatch,'beka_mak');before=deepcopy(db.card)
    db.fail_observation=True
    with pytest.raises(RuntimeError,match='INJECTED'):write(repo,replace(card,availability_normalized='out_of_stock'),obs,cap)
    assert db.card==before and not db.captures and not db.history and db.catalog==1282


def test_runtime_requires_its_running_queue_job(monkeypatch):
    repo,db,card,obs,cap=backend(monkeypatch,'beka_mak');db.run_exists=False
    with pytest.raises(RuntimeError,match='RUNNING_JOB_REQUIRED'):write(repo,card,obs,cap)
    assert not db.captures


@pytest.mark.parametrize('code',SOURCES)
def test_backend_passes_scoped_run_only_for_wrappers(code):
    b=object.__new__(SourceBackend);b.code=code;b.run_id=12;b.repo=Mock()
    b.persist('parsed','capture')
    call=b.repo.persist_kami_observation if code=='kami' else b.repo.persist_commercial_observation
    call.assert_called_once_with('parsed','capture',source_run_id=12)


@pytest.mark.parametrize('code',SOURCES)
def test_new_unlinked_fixture_inserts_only_one_source_and_offer(monkeypatch,code):
    repo,db,card,obs,cap=backend(monkeypatch,code,False)
    db.existing=db.offer_exists=False
    result=write(repo,card,obs,cap)
    assert db.new_products==db.new_offers==1 and db.catalog is None and db.active is False
    assert result['ingest_counts'].new_products==result['ingest_counts'].new_offers==1
    write(repo,card,obs,cap)
    assert db.new_products==db.new_offers==1 and len(db.captures)==1 and not db.history


@pytest.mark.parametrize('code',['kami','intervesp','vekprom','beka_mak'])
def test_content_change_persists_characteristics_and_history(monkeypatch,code):
    from universal_supplier.models import PropertyValue
    repo,db,card,obs,cap=backend(monkeypatch,code)
    # A non-identity characteristic is allowed by the unchanged strict engine.
    changed=replace(card,description_text=card.description_text+' Обновлено описание.',
                    properties=card.properties+(PropertyValue('Комплектация поставки','Новая упаковка',position=len(card.properties)),))
    result=write(repo,changed,obs,cap)
    assert result['ingest_counts'].changed_products==1
    repo._replace_details.assert_called_once()
    assert db.card.properties[-1].value=='Новая упаковка' and db.catalog==1282
    assert {h[4] for h in db.history}=={'description','properties'}
    write(repo,changed,obs,replace(cap,observed_at=cap.observed_at+timedelta(hours=1)))
    assert len(db.history)==2


def test_unknown_price_stock_keeps_proven_projection_and_records_observation(monkeypatch):
    from universal_supplier.commercial import PriceState
    repo,db,card,obs,cap=backend(monkeypatch,'beka_mak')
    old=replace(card,price=Decimal('123'),currency='RUB',availability_raw='InStock',availability_normalized='in_stock',quantity=Decimal(4))
    db.card=old
    incoming=replace(card,price=None,currency='',availability_raw='',availability_normalized='unknown',quantity=None)
    missing=replace(obs,price_state=PriceState.MISSING,price=None,currency=None)
    write(repo,incoming,missing,cap)
    assert (db.card.price,db.card.quantity,db.card.availability_normalized)==(Decimal('123'),Decimal(4),'in_stock')
    assert not db.history and len(db.observations)==1 and db.observations[0][6]=='missing'


def test_older_or_other_region_capture_only_appends_ledger(monkeypatch):
    repo,db,card,obs,cap=backend(monkeypatch,'beka_mak')
    db.at=cap.observed_at+timedelta(days=1)
    before=deepcopy(db.card)
    result=write(repo,replace(card,availability_normalized='out_of_stock'),obs,cap)
    assert not result['projection_updated'] and db.card==before and not db.history
    result=write(repo,card,obs,replace(cap,observed_at=db.at+timedelta(days=1),region_code='other'))
    assert not result['projection_updated'] and db.card==before and len(db.observations)==2


@pytest.mark.parametrize('code',SOURCES)
def test_real_writer_receipt_then_checkpoint_resume_no_second_ingest(monkeypatch,tmp_path,code):
    from test_source_runtime import setup
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SUPPLIER_HTTP','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SOURCE_WRITES','YES')
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(tmp_path/'evidence'))
    repo,db,card,obs,cap=backend(monkeypatch,code)
    # Runtime timestamp is supplied by a fake clock, independent of fixture date.
    db.at=None
    b=object.__new__(SourceBackend);b.code=code;b.run_id=12;b.repo=repo
    b.start=lambda job,mode:12
    b.progress=lambda s:None;b.finish=lambda s:None;b.close=lambda:None
    b.matching=Mock(return_value={'status':'succeeded','matching_applied':False})
    run,job,_,http,url=setup(code,backend=b)
    result=run()
    assert result['status']=='succeeded',result
    assert result['sql_committed']==1 and len(db.captures)==1 and db.catalog==1282
    previous_http=list(http);previous_sql=len(db.observations)
    resume=replace(job,id=2,settings_snapshot={**job.settings_snapshot,'crawl_settings':{
        **job.settings_snapshot['crawl_settings'],'collection_mode':'RESUME',
        'resume_owner_job_id':1,'resume_input_sha256':result['input_sha256']}})
    again=run(resume)
    assert again['status']=='succeeded',again
    assert http==previous_http and len(db.observations)==previous_sql and len(db.captures)==1
    assert b.matching.call_count==1


def test_changed_external_identity_cannot_insert_duplicate_for_existing_url(monkeypatch):
    repo,db,card,obs,cap=backend(monkeypatch,'beka_mak',False)
    db.existing=False;db.url_collision=True
    with pytest.raises(RuntimeError,match='URL_IDENTITY_CONFLICT'):
        write(repo,replace(card,external_id='OTHER-ID'),obs,cap)
    assert db.new_products==db.new_offers==0 and not db.captures


def test_mapping_readback_mismatch_rolls_back_all_supplier_mutations(monkeypatch):
    repo,db,card,obs,cap=backend(monkeypatch,'beka_mak');db.tamper_readback=True
    with pytest.raises(RuntimeError,match='MAPPING_CONFLICT'):write(repo,card,obs,cap)
    assert db.catalog==1282 and not db.captures and not db.history


def test_concurrent_exact_ingests_share_one_capture_and_no_business_events(monkeypatch):
    repo,db,card,obs,cap=backend(monkeypatch,'beka_mak')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:write(repo,card,obs,cap),range(16)))
    assert sum(not r['exact_noop'] for r in results)==1
    assert len(db.captures)==len(db.observations)==1 and not db.history
    assert any('pg_advisory_xact_lock' in q for q,a in db.statements)
    assert any('FROM source_products WHERE supplier_id' in q and 'FOR UPDATE' in q for q,a in db.statements)


def test_new_critical_property_conflict_stops_linked_update(monkeypatch):
    from universal_supplier.models import PropertyValue
    repo,db,card,obs,cap=backend(monkeypatch,'beka_mak')
    bad=replace(card,properties=card.properties+(PropertyValue('Напряжение','220 В'),PropertyValue('Напряжение','380 В')))
    with pytest.raises(RuntimeError,match='SEMANTIC_CONFLICT'):write(repo,bad,obs,cap)
    assert db.catalog==1282 and not db.history and not db.captures


@pytest.mark.parametrize('catalog',[None,57453])
def test_registry_loader_filters_target_without_changing_default_contract(catalog):
    from universal_supplier.matching_service import load_registry_from_postgres
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,q,args=()):
            self.rows=[]
            if 'FROM sterbrust_products WHERE' in q:
                assert 'catalog_product_id=%s' in q and args==(catalog,catalog)
                self.rows=[('145677','IRONMAC ITX-508MY','IRONMAC','ITX-508MY','','',{'items':[]})]
        def fetchall(self):return self.rows
    connection=Mock();connection.cursor.side_effect=Cursor
    result=load_registry_from_postgres(connection,catalog_product_id=catalog)
    assert set(result.products)=={'145677'}


def test_stored_options_and_media_participate_in_actual_history_baseline(monkeypatch):
    from universal_supplier.models import ProductOption,OptionValue,MediaItem
    from universal_supplier.source_update import stored_details
    repo,db,card,obs,cap=backend(monkeypatch,'kami')
    db.card=replace(card,options=(ProductOption('Исполнение',(OptionValue('Стандарт',selected=True),)),),
                    media=(MediaItem('image','https://www.stanki.ru/image.jpg'),))
    loaded=stored_details(db,replace(db.card,options=(),media=()),7)
    assert loaded.options==db.card.options and loaded.media==db.card.media
