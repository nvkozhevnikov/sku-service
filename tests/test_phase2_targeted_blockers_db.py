"""Actual operator/repository/selection transitions ONLY in approved scratch."""
import uuid,time,threading
from datetime import datetime,timezone
from copy import deepcopy
import pytest
from tests.test_kami_lifecycle_db import fixture,confirm_overlay,transition,link,feed_pair,POLICY,ROOT
from universal_supplier.postgres import PostgresRepository
from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.control_plane.store import PostgresControlPlaneStore,ProductFilters
from universal_supplier.yml_feed import PostgresFeedStore,FeedPolicy,build_yml,FeedGenerationError
from universal_supplier.xml_export import generate_canonical_xml


def target(f,label='Target Y'):
    c=f['c'];cat=c.execute('INSERT INTO catalog_products(name) VALUES(%s) RETURNING id',(label,)).fetchone()[0]
    sid=str(90000000+cat)
    c.execute("""INSERT INTO sterbrust_products(sterbrust_product_id,catalog_product_id,name,normalized_name,model_raw,normalized_model,category_id,category_name,product_url,active,snapshot_hash,first_seen_at,last_seen_at,last_synced_at)
        VALUES(%s,%s,%s,'scratch target','M200Y','m200y','1','Scratch equipment','https://example.invalid/target/',true,'scratch',now(),now(),now())""",(sid,cat,label))
    return cat,sid


def accepted_y(f,sid,status='EXACT_MATCH'):
    return {**f['decision'],'sterbrust_product_id':sid,'status':status,'auto_accepted':True}


@pytest.mark.parametrize('status',['EXACT_MATCH','HIGH_CONFIDENCE_MATCH'])
@pytest.mark.parametrize('method',['SUPPLIER_ARTICLE_EXACT','EXISTING_LINK'])
def test_human_accepted_remap_preserves_effective_target(fixture,status,method):
    f=fixture;confirm_overlay(f);cat,sid=target(f);incoming={**accepted_y(f,sid,status),'match_method':method};original=deepcopy(incoming)
    assert f['repo'].persist_match(incoming,datetime.now(timezone.utc).isoformat())=='INSERTED_EVENT'
    assert incoming==original and link(f)==f['catalog']
    assert f['c'].execute('SELECT status,auto_accepted,catalog_product_id FROM product_matches WHERE source_product_id=%s AND is_current',(f['source'],)).fetchone()==('REVIEW',False,cat)
    before=f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]
    assert f['repo'].persist_match(incoming,datetime.now(timezone.utc).isoformat())=='VALIDATED_UNCHANGED'
    assert f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]==before
    f['store'].apply(f['store'].evaluate_all(POLICY));feed_pair(f)


def test_human_unchanged_accepted_fingerprint_cannot_restore_other_target(fixture):
    f=fixture;cat,sid=target(f);incoming=accepted_y(f,sid)
    f['repo'].persist_match(incoming,datetime.now(timezone.utc).isoformat())
    # Scratch-only fixture: late operator confirmation after a controlled unlink
    # while the prior automatic accepted event still remains current.
    f['c'].execute('UPDATE source_products SET catalog_product_id=NULL WHERE id=%s',(f['source'],))
    confirm_overlay(f)
    f['repo'].persist_match(incoming,datetime.now(timezone.utc).isoformat())
    assert link(f)==f['catalog']
    assert f['c'].execute('SELECT status,auto_accepted FROM product_matches WHERE source_product_id=%s AND is_current',(f['source'],)).fetchone()==('REVIEW',False)


def test_human_same_target_revalidation_history_stable(fixture):
    f=fixture;confirm_overlay(f)
    before=f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]
    assert f['repo'].persist_match(f['decision'],datetime.now(timezone.utc).isoformat())=='VALIDATED_UNCHANGED'
    assert link(f)==f['catalog']
    assert f['c'].execute('SELECT count(*) FROM product_matches WHERE source_product_id=%s',(f['source'],)).fetchone()[0]==before


@pytest.mark.parametrize('final',['MARKED_REVIEWED','MANUAL_ACCESSORY'])
def test_human_protected_review_then_supersession_rejects_stale_winner(fixture,final):
    f=fixture;case,uid=confirm_overlay(f);transition(f);f['store'].apply(f['store'].evaluate_all(POLICY));feed_pair(f)
    history=f['c'].execute('SELECT count(*) FROM review_decisions WHERE source_product_id=%s',(f['source'],)).fetchone()[0]
    PostgresAdminStore(f['config']).decide(case,uid,final,comment='Explicit final supersession')
    assert transition(f)=='VALIDATED_UNCHANGED'
    assert link(f)==f['catalog']  # historical physical reference, not acceptance
    assert f['external'] not in f['repo'].load_existing_link_contexts('partner_st')
    stale=PostgresFeedStore(f['c']).load_snapshot()
    with pytest.raises(FeedGenerationError):build_yml(stale,FeedPolicy.load(ROOT/'config/yml_feed.json'))
    assert not any(x['source_product_id']==f['source'] for x in generate_canonical_xml(stale).trace_rows)
    decisions=f['store'].evaluate_all(POLICY)
    assert next(d for d in decisions if d.catalog_product_id==f['catalog']).selection_status=='no_eligible_offer'
    old_others=f['c'].execute('SELECT to_jsonb(t) FROM catalog_offer_selection t WHERE catalog_product_id<>%s ORDER BY catalog_product_id',(f['catalog'],)).fetchall()
    f['store'].apply(decisions)
    assert f['store'].apply(f['store'].evaluate_all(POLICY))['changed']==0
    assert f['c'].execute('SELECT to_jsonb(t) FROM catalog_offer_selection t WHERE catalog_product_id<>%s ORDER BY catalog_product_id',(f['catalog'],)).fetchall()==old_others
    assert f['c'].execute('SELECT count(*) FROM review_decisions WHERE source_product_id=%s',(f['source'],)).fetchone()[0]==history+1


def test_human_explicit_replacement_after_supersession(fixture):
    f=fixture;case,uid=confirm_overlay(f);transition(f);cat,sid=target(f)
    PostgresAdminStore(f['config']).decide(case,uid,'MARKED_REVIEWED',comment='Supersede old confirmation')
    PostgresAdminStore(f['config']).decide(case,uid,'MANUAL_CONFIRMED',sterbrust_product_id=sid,comment='Explicit replacement Y')
    assert link(f)==cat
    ctx=f['repo'].load_existing_link_contexts('partner_st')[f['external']]
    assert ctx['manual_or_human_confirmed'] and ctx['sterbrust_product_id']==sid
    f['store'].apply(f['store'].evaluate_all(POLICY));feed_pair(f)


def test_human_superseded_auto_assignment_may_proceed(fixture):
    f=fixture;case,uid=confirm_overlay(f);transition(f);cat,sid=target(f)
    PostgresAdminStore(f['config']).decide(case,uid,'MARKED_REVIEWED',comment='Supersession')
    f['repo'].persist_match(accepted_y(f,sid),datetime.now(timezone.utc).isoformat());assert link(f)==cat
    f['store'].apply(f['store'].evaluate_all(POLICY));feed_pair(f)


def test_human_old_different_target_history_does_not_protect_new_target(fixture):
    f=fixture;case,uid=confirm_overlay(f);transition(f)
    PostgresAdminStore(f['config']).decide(case,uid,'MARKED_REVIEWED',comment='End X authority')
    y,yid=target(f,'Y');z,zid=target(f,'Z')
    f['repo'].persist_match(accepted_y(f,yid),datetime.now(timezone.utc).isoformat());assert link(f)==y
    f['repo'].persist_match(accepted_y(f,zid),datetime.now(timezone.utc).isoformat());assert link(f)==z


def test_human_unrelated_ledger_does_not_block_accepted_assignment(fixture):
    f=fixture;c=f['c'];y,yid=target(f)
    other=c.execute("""INSERT INTO source_products(supplier_id,external_id,external_id_is_stable,name,source_url,first_seen_at,last_seen_at,last_success_at)
        VALUES(%s,%s,true,'Other source','https://example.invalid/other/',now(),now(),now()) RETURNING id""",(f['other_supplier'],uuid.uuid4().hex)).fetchone()[0]
    uid=c.execute("INSERT INTO app_users(username,display_name,password_hash,role) VALUES(%s,'Operator','scrypt$fixture','OPERATOR') RETURNING id",('test_'+uuid.uuid4().hex,)).fetchone()[0]
    case=c.execute("""INSERT INTO review_cases(source_product_id,priority_rank,priority_reason,automatic_decision,source_identity_fingerprint,source_identity_snapshot)
        VALUES(%s,1,'other','REVIEW_EXISTING',%s,'{}') RETURNING id""",(other,'0'*64)).fetchone()[0]
    PostgresAdminStore(f['config']).decide(case,uid,'MANUAL_CONFIRMED',sterbrust_product_id=f['sid'],comment='Other source only')
    f['repo'].persist_match(accepted_y(f,yid),datetime.now(timezone.utc).isoformat())
    assert link(f)==y and c.execute('SELECT catalog_product_id FROM source_products WHERE id=%s',(other,)).fetchone()[0]==f['catalog']


def test_human_postponed_still_protects_accepted_remap(fixture):
    f=fixture;case,uid=confirm_overlay(f);_,sid=target(f)
    PostgresAdminStore(f['config']).decide(case,uid,'POSTPONED',comment='Not revocation')
    f['repo'].persist_match(accepted_y(f,sid),datetime.now(timezone.utc).isoformat());assert link(f)==f['catalog']


def test_human_accepted_remap_rollback(fixture):
    f=fixture;confirm_overlay(f);_,sid=target(f)
    def state():return [f['c'].execute('SELECT to_jsonb(t) FROM '+table+' t WHERE '+key+'=%s ORDER BY to_jsonb(t)::text',(f['source'],)).fetchall() for table,key in [('source_products','id'),('product_matches','source_product_id'),('review_decisions','source_product_id')]]
    before=state()
    with pytest.raises(RuntimeError):
        with f['c'].transaction():f['repo'].persist_match(accepted_y(f,sid),datetime.now(timezone.utc).isoformat());raise RuntimeError('scratch rollback')
    assert state()==before


def test_human_confirm_commit_serializes_before_pending_accepted_result(fixture):
    f=fixture;c=f['c'];_,sid=target(f);ready=threading.Event();release=threading.Event();started=threading.Event();errors=[];pids={}
    uid=c.execute("INSERT INTO app_users(username,display_name,password_hash,role) VALUES(%s,'Scratch operator','scrypt$fixture','OPERATOR') RETURNING id",('test_'+uuid.uuid4().hex,)).fetchone()[0]
    case=c.execute("""INSERT INTO review_cases(source_product_id,priority_rank,priority_reason,automatic_decision,source_identity_fingerprint,source_identity_snapshot)
        VALUES(%s,1,'concurrency fixture','REVIEW_EXISTING',%s,'{}') RETURNING id""",(f['source'],'0'*64)).fetchone()[0]
    class PausedAdmin(PostgresAdminStore):
        def _audit(self,*args,**kwargs):
            super()._audit(*args,**kwargs);ready.set()
            if not release.wait(6):raise RuntimeError('controlled pause timeout')
    def human():
        try:PausedAdmin(f['config']).decide(case,uid,'MANUAL_CONFIRMED',sterbrust_product_id=f['sid'],comment='Committed before pending matcher')
        except Exception as exc:errors.append(type(exc).__name__)
    def matcher():
        r=PostgresRepository(f['config'])
        try:
            pids['matcher']=r.connection.execute('SELECT pg_backend_pid()').fetchone()[0];started.set()
            r.persist_match(accepted_y(f,sid),datetime.now(timezone.utc).isoformat())
        except Exception as exc:errors.append(type(exc).__name__)
        finally:r.close()
    h=threading.Thread(target=human);m=threading.Thread(target=matcher)
    h.start()
    try:
        assert ready.wait(4);m.start();assert started.wait(3)
        blocked=False
        for _ in range(60):
            if c.execute('SELECT cardinality(pg_blocking_pids(%s))',(pids['matcher'],)).fetchone()[0]>0:blocked=True;break
            time.sleep(.02)
        assert blocked,'Matcher must serialize on operator case lock'
    finally:release.set();h.join(6);m.join(6)
    assert not errors and not h.is_alive() and not m.is_alive() and link(f)==f['catalog']


@pytest.mark.parametrize('external',['300','30','3000','opaque_%_ID'])
def test_exact_lookup_namespace_and_neighbour_ids(fixture,external):
    f=fixture;c=f['c'];ids={}
    for sup in (f['supplier'],f['other_supplier']):
        for key in ('300','30','3000','opaque_%_ID'):
            ids[(sup,key)]=c.execute("""INSERT INTO source_products(supplier_id,external_id,external_id_is_stable,name,source_url,first_seen_at,last_seen_at,last_success_at)
                VALUES(%s,%s,true,'Neighbour300 machine','https://example.invalid/lookup/',now(),now(),now()) RETURNING id""",(sup,key)).fetchone()[0]
    # More substring matches than one page must not affect exact detail.
    for i in range(35):c.execute("""INSERT INTO source_products(supplier_id,external_id,external_id_is_stable,name,source_url,first_seen_at,last_seen_at,last_success_at)
        VALUES(%s,%s,true,'AAA neighbour300','https://example.invalid/lookup/',now(),now(),now())""",(f['supplier'],'prefix300-'+str(i)+'-'+uuid.uuid4().hex))
    store=PostgresControlPlaneStore(f['config'])
    # Existing fixture rolls back; store needs the same transaction connection,
    # use a context wrapper that does not close/commit its caller-owned session.
    from contextlib import nullcontext
    from psycopg.rows import dict_row
    original=c.row_factory;c.row_factory=dict_row
    store._connect=lambda:nullcontext(c)
    try:
        a=store.product('partner_st',external)
        # General paginated search can be unusable without changing exact detail.
        store.products=lambda *_:(_ for _ in ()).throw(AssertionError('No display search in exact lookup'))
        assert store.product('partner_st',external)['id']==a['id']
        code=c.execute('SELECT code FROM suppliers WHERE id=%s',(f['other_supplier'],)).fetchone()['code']
        b=store.product(code,external)
        assert a['id']==ids[(f['supplier'],external)] and b['id']==ids[(f['other_supplier'],external)]
        assert a['external_id']==external and b['external_id']==external
        c.execute('UPDATE source_products SET active=false WHERE id=%s',(a['id'],))
        assert store.product('partner_st',external) is None
    finally:c.row_factory=original
