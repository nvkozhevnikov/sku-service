"""Real PostgreSQL lifecycle regressions, destructive fixtures ONLY in scratch.

No HTTP, no RC writes. Opt-in database is pinned to a fresh scratch receipt.
Manual-overlay regression uses the existing operator API, not hand-made flags.
"""
import json,os,uuid
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path

import pytest
import psycopg
from universal_supplier.postgres import PostgresConfig,PostgresRepository
from universal_supplier.offer_selection import PostgresOfferSelectionStore,OfferSelectionPolicy
from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.proposed_offer_selection import proposed_selection
from universal_supplier.selection_reconciliation import apply_diff
from universal_supplier.yml_feed import PostgresFeedStore,FeedPolicy,build_yml
from universal_supplier.yml_feed import selected_row_violations
from universal_supplier.xml_export import generate_canonical_xml
from universal_supplier.control_plane.store import PostgresControlPlaneStore,ProductFilters
from scripts.recover_kami_selection_predelete import exact_missing

ROOT=Path(__file__).resolve().parents[1]
DB='universal_supplier_kami_lifecycle_test_20261006'
POLICY=OfferSelectionPolicy.load(ROOT/'config/offer_selection.json')


class ScratchConfig(PostgresConfig):
    def __repr__(self):
        return 'ScratchConfig(database='+self.dbname+', credentials=[REDACTED])'


@pytest.fixture
def fixture(request):
    if os.environ.get('KAMI_LIFECYCLE_TEST_DB')!=DB:
        pytest.skip('Explicit scratch DB opt-in required; never use RC')
    receipt=json.loads((ROOT/'reports/KAMI_INTEGRATION_2026-10-05/CANONICAL_AUTHORITY_RC_2026-10-06/SCRATCH_READY.json').read_bytes())
    assert receipt['database']==DB and receipt['system_identifier']=='7693098812512405428'
    private=Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005/runtime.json'
    values=json.loads(private.read_bytes())
    config=ScratchConfig('127.0.0.1',55451,DB,'kami_app',values['DB_PASSWORD'],'disable')
    repo=PostgresRepository(config);c=repo.connection
    assert c.execute('SELECT current_database(),inet_server_port(),current_user').fetchone()==(DB,55451,'kami_app')
    # Earlier synthetic seeds lacked the feed's required declared price role.
    # Repair only these scratch fixtures, never infer a real supplier price role.
    c.execute("UPDATE offers SET price_type='retail' WHERE price_type='unknown' AND source_product_id IN (SELECT id FROM source_products WHERE source_url='https://example.invalid/source/')")
    token=uuid.uuid4().hex
    # Seed using supported schema defaults, generated identities, no reset/truncate.
    supplier=c.execute("INSERT INTO suppliers(code,name,base_url,adapter_name) VALUES(%s,'Scratch supplier','https://example.invalid/','test') RETURNING id",('test_'+token,)).fetchone()[0]
    catalog=c.execute("INSERT INTO catalog_products(name) VALUES('Scratch TEST-M200') RETURNING id").fetchone()[0]
    sid=str(90000000+catalog)
    c.execute("""INSERT INTO sterbrust_products(sterbrust_product_id,catalog_product_id,name,normalized_name,model_raw,normalized_model,category_id,category_name,product_url,active,snapshot_hash,first_seen_at,last_seen_at,last_synced_at)
        VALUES(%s,%s,'Scratch TEST-M200','scratch test m200','TEST-M200','testm200','1','Scratch equipment','https://example.invalid/product/',true,'scratch',now(),now(),now())""",(sid,catalog))
    # persist_match addresses only supported supplier namespace. Use one shared
    # real schema supplier, unique source external IDs per test.
    c.execute("INSERT INTO suppliers(code,name,base_url,adapter_name) VALUES('partner_st','Scratch Partner','https://example.invalid/','test') ON CONFLICT(code) DO NOTHING")
    partner=c.execute("SELECT id FROM suppliers WHERE code='partner_st'").fetchone()[0]
    source=c.execute("""INSERT INTO source_products(supplier_id,external_id,external_id_is_stable,name,source_url,first_seen_at,last_seen_at,last_success_at)
        VALUES(%s,%s,true,'Scratch TEST-M200','https://example.invalid/source/',now(),now(),now()) RETURNING id""",(partner,token)).fetchone()[0]
    offer=c.execute("""INSERT INTO offers(supplier_id,source_product_id,price,price_type,currency,quantity,availability_normalized,first_seen_at,last_seen_at,last_success_at)
        VALUES(%s,%s,100,'retail','RUB',2,'in_stock',now(),now(),now()) RETURNING id""",(partner,source)).fetchone()[0]
    c.execute("INSERT INTO crawl_runs(supplier_id,crawl_type,status,started_at,finished_at) VALUES(%s,'full','succeeded','2026-10-06 00:00:00+00',now())",(partner,))
    c.execute('UPDATE source_products SET last_success_at=now() WHERE id=%s',(source,))
    c.execute('UPDATE offers SET last_success_at=now() WHERE id=%s',(offer,))
    decision={'source_external_id':token,'sterbrust_product_id':sid,'status':'EXACT_MATCH','match_method':'SUPPLIER_ARTICLE_EXACT',
              'confidence_score':1,'auto_accepted':True,'evidence':{'typed_anchor':'synthetic observed','raw_provenance':'scratch fixture'},'conflicts':{},'warnings':{}}
    repo.persist_match(decision,datetime.now(timezone.utc).isoformat())
    store=PostgresOfferSelectionStore(c);store.apply(store.evaluate_all(POLICY))
    data={'repo':repo,'c':c,'config':config,'catalog':catalog,'sid':sid,'source':source,'supplier':partner,'other_supplier':supplier,'external':token,'offer':offer,'decision':decision,'store':store}
    try:
        if request.node.name.startswith('test_human_'):
            # Operator API uses a distinct connection; retain this deliberate
            # committed scratch scenario for evidence if the guard fails.
            yield data
        else:
            # Keep other tests independent without truncation/drop or deleting
            # append-only evidence; the seed remains a valid accepted fixture.
            with c.transaction():
                yield data
                raise psycopg.Rollback()
    finally:repo.close()


def transition(f,status='REVIEW'):
    decision={**f['decision'],'status':status,'match_method':'SCRATCH_REAL_NEW_EVIDENCE','auto_accepted':False,
              'evidence':{'typed_anchor':'new contradictory synthetic evidence','raw_provenance':'scratch fixture'},'conflicts':{'synthetic':status}}
    return f['repo'].persist_match(decision,datetime.now(timezone.utc).isoformat())


def link(f):return f['c'].execute('SELECT catalog_product_id FROM source_products WHERE id=%s',(f['source'],)).fetchone()[0]


@pytest.mark.parametrize('status',['REVIEW','CONFLICT'])
def test_real_persisted_nonaccepted_quarantines_auto(fixture,status):
    f=fixture;assert link(f)==f['catalog'];transition(f,status);assert link(f) is None
    assert f['c'].execute('SELECT status,auto_accepted FROM product_matches WHERE source_product_id=%s AND is_current',(f['source'],)).fetchone()==(status,False)


def test_manual_current_decision_never_quarantined(fixture):
    f=fixture
    f['repo'].persist_match({**f['decision'],'match_method':'EXISTING_LINK_MANUAL','auto_accepted':False},datetime.now(timezone.utc).isoformat())
    transition(f);assert link(f)==f['catalog']


def test_missing_label_does_not_erase_auto_lineage(fixture):
    f=fixture;ctx=f['repo'].load_existing_link_contexts('partner_st')[f['external']]
    assert ctx['current_auto_accepted'] and not ctx['manual_or_human_confirmed']
    assert ctx['origin_match_method']=='SUPPLIER_ARTICLE_EXACT'


def test_unchanged_fingerprint_returns_no_new_history(fixture):
    f=fixture;before=f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]
    assert f['repo'].persist_match(f['decision'],datetime.now(timezone.utc).isoformat())=='VALIDATED_UNCHANGED'
    assert f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]==before


def test_repeat_review_is_semantically_idempotent(fixture):
    f=fixture;transition(f);before=f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]
    assert transition(f)=='VALIDATED_UNCHANGED';assert link(f) is None
    assert f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]==before


def test_quarantine_preserves_history_and_source_offer(fixture):
    f=fixture;old=f['c'].execute('SELECT id,evidence FROM product_matches WHERE source_product_id=%s AND is_current',(f['source'],)).fetchone()
    transition(f)
    assert f['c'].execute('SELECT evidence,is_current FROM product_matches WHERE id=%s',(old[0],)).fetchone()==(old[1],False)
    assert f['c'].execute('SELECT count(*) FROM source_products WHERE id=%s',(f['source'],)).fetchone()[0]==1
    assert f['c'].execute('SELECT count(*) FROM offers WHERE id=%s',(f['offer'],)).fetchone()[0]==1


def test_quarantine_only_intended_link(fixture):
    f=fixture;other=f['c'].execute("INSERT INTO catalog_products(name) VALUES('Other scratch product') RETURNING id").fetchone()[0]
    f['c'].execute('UPDATE source_products SET catalog_product_id=%s WHERE id=%s',(other,f['source']))
    transition(f);assert link(f)==other


def test_unrelated_supplier_unchanged(fixture):
    f=fixture;c=f['c']
    other=c.execute("""INSERT INTO source_products(supplier_id,external_id,external_id_is_stable,name,source_url,catalog_product_id,first_seen_at,last_seen_at,last_success_at)
        VALUES(%s,%s,true,'Other scratch source','https://example.invalid/other/',%s,now(),now(),now()) RETURNING id""",
        (f['other_supplier'],uuid.uuid4().hex,f['catalog'])).fetchone()[0]
    before=c.execute('SELECT to_jsonb(t) FROM suppliers t WHERE id=%s',(f['other_supplier'],)).fetchone()[0]
    source_before=c.execute('SELECT to_jsonb(t) FROM source_products t WHERE id=%s',(other,)).fetchone()[0]
    transition(f)
    assert c.execute('SELECT to_jsonb(t) FROM suppliers t WHERE id=%s',(f['other_supplier'],)).fetchone()[0]==before
    assert c.execute('SELECT to_jsonb(t) FROM source_products t WHERE id=%s',(other,)).fetchone()[0]==source_before


def test_quarantine_to_canonical_apply_retains_lifecycle_row(fixture):
    f=fixture;transition(f);decisions=f['store'].evaluate_all(POLICY)
    desired=next(d for d in decisions if d.catalog_product_id==f['catalog']);assert desired.selection_status=='no_eligible_offer'
    f['store'].apply(decisions)
    assert f['c'].execute('SELECT selection_status,selected_offer_id FROM catalog_offer_selection WHERE catalog_product_id=%s',(f['catalog'],)).fetchone()==('no_eligible_offer',None)


def test_feed_after_legitimate_quarantine_apply(fixture):
    f=fixture;transition(f);f['store'].apply(f['store'].evaluate_all(POLICY))
    snapshot=PostgresFeedStore(f['c']).load_snapshot();artifact=build_yml(snapshot,FeedPolicy.load(ROOT/'config/yml_feed.json'))
    assert artifact is not None
    assert all(r.source_product_id!=f['source'] for r in snapshot.selected_rows)


def test_matching_quarantine_rollback(fixture):
    f=fixture;old=f['c'].execute('SELECT to_jsonb(t) FROM product_matches t WHERE source_product_id=%s ORDER BY id',(f['source'],)).fetchall()
    with pytest.raises(RuntimeError):
        with f['c'].transaction():transition(f);raise RuntimeError('deliberate scratch rollback')
    assert link(f)==f['catalog']
    assert f['c'].execute('SELECT to_jsonb(t) FROM product_matches t WHERE source_product_id=%s ORDER BY id',(f['source'],)).fetchall()==old


def test_canonical_apply_twice_noop(fixture):
    f=fixture;f['store'].apply(f['store'].evaluate_all(POLICY));second=f['store'].apply(f['store'].evaluate_all(POLICY));assert second['changed']==0


def test_recovery_plan_guarded_noop_from_actual_db(fixture):
    f=fixture;rows=[r[0] for r in f['c'].execute('SELECT to_jsonb(t) FROM catalog_offer_selection t ORDER BY catalog_product_id')]
    assert exact_missing(rows,rows,{f['catalog']})==[]
    assert [r[0] for r in f['c'].execute('SELECT to_jsonb(t) FROM catalog_offer_selection t ORDER BY catalog_product_id')]==rows


def test_ordinary_reconciliation_no_SQL_or_DELETE(fixture):
    f=fixture;before=f['c'].execute('SELECT count(*) FROM catalog_offer_selection').fetchone()[0]
    with pytest.raises(ValueError,match='removed'):apply_diff(f['c'],[{'action':'DELETE','catalog_product_id':f['catalog']}],{})
    assert f['c'].execute('SELECT count(*) FROM catalog_offer_selection').fetchone()[0]==before


def test_advisory_cannot_mutate_matching_or_link(fixture):
    f=fixture;before=f['c'].execute('SELECT to_jsonb(t) FROM product_matches t WHERE source_product_id=%s ORDER BY id',(f['source'],)).fetchall()
    assert proposed_selection([{'source':'partner_st','external_id':f['external'],'classification':'REVIEW'}])['selected']==[]
    assert link(f)==f['catalog']
    assert f['c'].execute('SELECT to_jsonb(t) FROM product_matches t WHERE source_product_id=%s ORDER BY id',(f['source'],)).fetchall()==before


def test_feed_basis_reads_persisted_not_advisory(fixture):
    f=fixture;snapshot=PostgresFeedStore(f['c']).load_snapshot()
    assert any(r.source_product_id==f['source'] and r.selected_offer_id==f['offer'] for r in snapshot.selected_rows)
    assert proposed_selection([{'source':'partner_st','external_id':f['external'],'classification':'REVIEW'}])['selected']==[]


def test_human_confirmed_overlay_survives_new_REVIEW(fixture):
    """F-P3-06: existing operator API can confirm the same automatic target."""
    f=fixture
    confirm_overlay(f)
    transition(f)
    assert link(f)==f['catalog'],'CURRENT_DEFECT: real MANUAL_CONFIRMED same-product overlay was quarantined'


def confirm_overlay(f):
    c=f['c']
    uid=c.execute("INSERT INTO app_users(username,display_name,password_hash,role) VALUES(%s,'Scratch operator','scrypt$synthetic_fixture','OPERATOR') RETURNING id",('test_'+uuid.uuid4().hex,)).fetchone()[0]
    case=c.execute("""INSERT INTO review_cases(source_product_id,priority_rank,priority_reason,automatic_decision,source_identity_fingerprint,source_identity_snapshot)
        VALUES(%s,1,'scratch manual confirm','REVIEW_EXISTING',%s,'{}') RETURNING id""",(f['source'],'0'*64)).fetchone()[0]
    PostgresAdminStore(f['config']).decide(case,uid,'MANUAL_CONFIRMED',sterbrust_product_id=f['sid'],comment='Explicit synthetic human confirmation')
    assert f['repo'].load_existing_link_contexts('partner_st')[f['external']]['manual_or_human_confirmed'] is True
    return case,uid


def test_human_overlay_conflict_and_repeat_preserves_history(fixture):
    f=fixture;confirm_overlay(f);transition(f,'CONFLICT')
    before=f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]
    assert transition(f,'CONFLICT')=='VALIDATED_UNCHANGED'
    assert link(f)==f['catalog']
    assert f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]==before


def test_human_overlay_different_target_not_protected(fixture):
    f=fixture;confirm_overlay(f)
    # Simulate a later automatic physical target X while manual ledger refers Y.
    old=f['catalog']
    other=f['c'].execute("INSERT INTO catalog_products(name) VALUES('Different scratch target') RETURNING id").fetchone()[0]
    f['c'].execute('UPDATE source_products SET catalog_product_id=%s WHERE id=%s',(other,f['source']))
    f['c'].execute('UPDATE product_matches SET catalog_product_id=%s WHERE source_product_id=%s AND is_current',(other,f['source']))
    stale=next(r for r in PostgresFeedStore(f['c']).load_snapshot().selected_rows if r.source_product_id==f['source'])
    assert stale.effective_manual_source_product_id is None
    assert any(v.startswith('SOURCE_CATALOG_PRODUCT_MISMATCH') for v in selected_row_violations((stale,),FeedPolicy.load(ROOT/'config/yml_feed.json'),1))
    transition(f);assert link(f) is None
    assert old!=other


@pytest.mark.parametrize('superseding',['MANUAL_ACCESSORY','MARKED_REVIEWED'])
def test_human_overlay_superseded_not_protected(fixture,superseding):
    f=fixture;case,uid=confirm_overlay(f)
    PostgresAdminStore(f['config']).decide(case,uid,superseding,comment='Superseding synthetic decision')
    assert not f['repo'].load_existing_link_contexts('partner_st')[f['external']]['manual_or_human_confirmed']
    transition(f);assert link(f) is None
    assert f['c'].execute('SELECT count(*) FROM review_decisions WHERE source_product_id=%s',(f['source'],)).fetchone()[0]==2


def test_human_overlay_postponed_is_not_revocation(fixture):
    f=fixture;case,uid=confirm_overlay(f)
    PostgresAdminStore(f['config']).decide(case,uid,'POSTPONED',comment='Queue scheduling only')
    transition(f);assert link(f)==f['catalog']


def test_human_overlay_review_repeat_and_selection(fixture):
    f=fixture;confirm_overlay(f);transition(f)
    before=f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]
    assert transition(f)=='VALIDATED_UNCHANGED';assert link(f)==f['catalog']
    assert f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]==before
    desired=next(d for d in f['store'].evaluate_all(POLICY) if d.catalog_product_id==f['catalog'])
    # Canonical selection deliberately reads the preserved physical mapping.
    assert desired.selection_status=='selected' and desired.selected_offer_id==f['offer']
    f['store'].apply(f['store'].evaluate_all(POLICY))
    assert f['store'].apply(f['store'].evaluate_all(POLICY))['changed']==0


def test_human_overlay_rollback_exact(fixture):
    f=fixture;confirm_overlay(f)
    queries=[('source_products','id'),('product_matches','source_product_id'),('review_decisions','source_product_id')]
    rows=lambda:[f['c'].execute(f'SELECT to_jsonb(t) FROM {table} t WHERE {key}=%s ORDER BY to_jsonb(t)::text',(f['source'],)).fetchall() for table,key in queries]
    before=rows()
    with pytest.raises(RuntimeError):
        with f['c'].transaction():transition(f);raise RuntimeError('deliberate rollback')
    assert rows()==before


def test_auto_new_candidate_quarantine(fixture):
    f=fixture;transition(f,'NEW_CANDIDATE');assert link(f) is None


def feed_pair(f):
    snapshot=PostgresFeedStore(f['c']).load_snapshot()
    yml=build_yml(snapshot,FeedPolicy.load(ROOT/'config/yml_feed.json'))
    xml=generate_canonical_xml(snapshot)
    assert yml.validation.valid and xml.validation.valid
    assert any(r.source_product_id==f['source'] for r in snapshot.selected_rows)
    return snapshot,yml,xml


@pytest.mark.parametrize('status',['REVIEW','CONFLICT','NEW_CANDIDATE'])
def test_human_effective_identity_yml_xml_admin_agree(fixture,status):
    f=fixture;confirm_overlay(f);transition(f,status)
    assert link(f)==f['catalog']
    f['store'].apply(f['store'].evaluate_all(POLICY))
    snapshot,yml,xml=feed_pair(f)
    row=next(r for r in snapshot.selected_rows if r.source_product_id==f['source'])
    assert row.current_match_status==status
    assert row.effective_manual_source_product_id==f['source']
    assert row.effective_manual_catalog_product_id==f['catalog']
    visible=next(r for r in PostgresControlPlaneStore(f['config']).iter_export_rows(ProductFilters()) if r['source_product_id']==f['source'])
    assert visible['confirmed_sterbrust_id']==f['sid'] and visible['confirmed_link_source']=='MANUAL_CONFIRMED'
    assert build_yml(snapshot,FeedPolicy.load(ROOT/'config/yml_feed.json')).data==yml.data
    assert generate_canonical_xml(snapshot).data==xml.data


def test_human_postponed_yml_xml_agree(fixture):
    f=fixture;case,uid=confirm_overlay(f)
    PostgresAdminStore(f['config']).decide(case,uid,'POSTPONED',comment='Queue scheduling')
    transition(f);f['store'].apply(f['store'].evaluate_all(POLICY));feed_pair(f)


def test_direct_accepted_current_mapping_feed_pair(fixture):
    f=fixture;f['repo'].persist_match({**f['decision'],'match_method':'EXISTING_LINK_MANUAL','auto_accepted':False},datetime.now(timezone.utc).isoformat())
    feed_pair(f)


@pytest.mark.parametrize('status',['REVIEW','CONFLICT'])
def test_unprotected_stale_winner_rejected_by_yml_and_xml(fixture,status):
    f=fixture
    # Deliberate invalid stale selection: physical link exists, no accepted
    # current match/manual ledger. Never a valid operational mapping.
    f['c'].execute('UPDATE product_matches SET status=%s,auto_accepted=false WHERE source_product_id=%s AND is_current',(status,f['source']))
    assert not f['repo'].load_existing_link_contexts('partner_st')[f['external']]['manual_or_human_confirmed']
    snapshot=PostgresFeedStore(f['c']).load_snapshot()
    row=next(r for r in snapshot.selected_rows if r.source_product_id==f['source'])
    assert any(v.startswith('NO_CURRENT_ACCEPTED_MATCH') for v in selected_row_violations((row,),FeedPolicy.load(ROOT/'config/yml_feed.json'),1))
    xml=generate_canonical_xml(snapshot)
    assert not any(r['source_product_id']==f['source'] for r in xml.trace_rows)


def test_human_superseded_ledger_stale_winner_rejected(fixture):
    f=fixture;case,uid=confirm_overlay(f)
    PostgresAdminStore(f['config']).decide(case,uid,'MARKED_REVIEWED',comment='Superseding final disposition')
    transition(f)
    # Deliberately stale physical link/selection for a negative feed assertion;
    # current match fingerprint is still generated by the real repository API.
    f['c'].execute('UPDATE source_products SET catalog_product_id=%s WHERE id=%s',(f['catalog'],f['source']))
    snapshot=PostgresFeedStore(f['c']).load_snapshot()
    row=next(r for r in snapshot.selected_rows if r.source_product_id==f['source'])
    assert row.effective_manual_source_product_id is None
    assert any(v.startswith('NO_CURRENT_ACCEPTED_MATCH') for v in selected_row_violations((row,),FeedPolicy.load(ROOT/'config/yml_feed.json'),1))
    # Do not leak a deliberately corrupted negative fixture into later tests.
    # Restore a valid accepted automatic scratch state; manual remains superseded.
    f['repo'].persist_match(f['decision'],datetime.now(timezone.utc).isoformat())
    f['store'].apply(f['store'].evaluate_all(POLICY))


def test_stale_offer_removed_by_unchanged_selection_policy(fixture):
    f=fixture
    f['c'].execute("UPDATE offers SET last_success_at='2000-01-01' WHERE id=%s",(f['offer'],))
    f['store'].apply(f['store'].evaluate_all(POLICY))
    snapshot=PostgresFeedStore(f['c']).load_snapshot()
    assert all(r.source_product_id!=f['source'] for r in snapshot.selected_rows)
    assert build_yml(snapshot,FeedPolicy.load(ROOT/'config/yml_feed.json')).validation.valid


def test_human_unrelated_source_confirmation_no_effect(fixture):
    f=fixture;c=f['c']
    other=c.execute("""INSERT INTO source_products(supplier_id,external_id,external_id_is_stable,name,source_url,first_seen_at,last_seen_at,last_success_at)
        VALUES(%s,%s,true,'Unrelated source','https://example.invalid/other/',now(),now(),now()) RETURNING id""",(f['other_supplier'],uuid.uuid4().hex)).fetchone()[0]
    uid=c.execute("INSERT INTO app_users(username,display_name,password_hash,role) VALUES(%s,'Scratch operator','scrypt$synthetic_fixture','OPERATOR') RETURNING id",('test_'+uuid.uuid4().hex,)).fetchone()[0]
    case=c.execute("""INSERT INTO review_cases(source_product_id,priority_rank,priority_reason,automatic_decision,source_identity_fingerprint,source_identity_snapshot)
        VALUES(%s,1,'unrelated synthetic','REVIEW_EXISTING',%s,'{}') RETURNING id""",(other,'0'*64)).fetchone()[0]
    PostgresAdminStore(f['config']).decide(case,uid,'MANUAL_CONFIRMED',sterbrust_product_id=f['sid'],comment='Other source only')
    transition(f);assert link(f) is None
    # Deliberate stale relation for negative feed check; no effective ledger here.
    c.execute('UPDATE source_products SET catalog_product_id=%s WHERE id=%s',(f['catalog'],f['source']))
    row=next(r for r in PostgresFeedStore(c).load_snapshot().selected_rows if r.source_product_id==f['source'])
    assert row.effective_manual_source_product_id is None
    assert any(v.startswith('NO_CURRENT_ACCEPTED_MATCH') for v in selected_row_violations((row,),FeedPolicy.load(ROOT/'config/yml_feed.json'),1))
    assert c.execute('SELECT catalog_product_id FROM source_products WHERE id=%s',(other,)).fetchone()[0]==f['catalog']
    f['repo'].persist_match(f['decision'],datetime.now(timezone.utc).isoformat())


def test_human_authority_over_different_automatic_candidate(fixture):
    f=fixture;c=f['c'];confirm_overlay(f)
    catalog=c.execute("INSERT INTO catalog_products(name) VALUES('Unaccepted alternative scratch target') RETURNING id").fetchone()[0]
    sid=str(90000000+catalog)
    c.execute("""INSERT INTO sterbrust_products(sterbrust_product_id,catalog_product_id,name,normalized_name,model_raw,normalized_model,category_id,category_name,product_url,active,snapshot_hash,first_seen_at,last_seen_at,last_synced_at)
        VALUES(%s,%s,'Alternative scratch','alternative','ALT','alt','1','Scratch equipment','https://example.invalid/alternative/',true,'scratch',now(),now(),now())""",(sid,catalog))
    f['repo'].persist_match({**f['decision'],'sterbrust_product_id':sid,'status':'REVIEW','auto_accepted':False,'match_method':'SCRATCH_ALTERNATIVE_UNCONFIRMED'},datetime.now(timezone.utc).isoformat())
    assert link(f)==f['catalog']
    f['store'].apply(f['store'].evaluate_all(POLICY))
    snapshot,_,_=feed_pair(f)
    selected=next(r for r in snapshot.selected_rows if r.source_product_id==f['source'])
    assert selected.current_match_catalog_product_id==catalog
    assert selected.effective_manual_catalog_product_id==f['catalog']
