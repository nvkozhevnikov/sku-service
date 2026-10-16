"""Bounded single-worker LIVE canary; frozen candidates, fail-closed source gates."""
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
import hashlib
import json
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.run_ltt_uss_offline_canary import fingerprints, save
from universal_supplier.adapters.ltt import parse_ltt_modx_detail
from universal_supplier.adapters.ltt_yug import parse_ltt_yug_detail
from universal_supplier.adapters.uss_stanko import parse_uss_stanko_detail
from universal_supplier.commercial_persistence import normalize_availability
from universal_supplier.full_supplier_collection import PublicPacer
from universal_supplier.ltt_uss_capture import capture_source_html
from universal_supplier.http_capture import CaptureStatus
from universal_supplier.postgres import PostgresConfig,PostgresRepository
from sterbrust_matching.product_identity import TAXON_BY_KIND, ACCESSORY_FAMILIES

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/LTT_USS_INTEGRATION_2026-10-13'
OUT=BASE/'RESUME_2026-10-14'
SOURCES=('ltt_msk','ltt_sib','ltt_yug','uss_stanko')
ALLOWED={'suppliers','source_products','offers','supplier_http_captures','offer_commercial_observations'}


def parse(supplier,body,url):
    source=body.decode('utf-8')
    if supplier in {'ltt_msk','ltt_sib'}:
        return parse_ltt_modx_detail(source,supplier=supplier,source_url=url)
    if supplier=='ltt_yug':
        return parse_ltt_yug_detail(source,source_url=url)
    return parse_uss_stanko_detail(source,source_url=url)


def plan():
    audit=json.loads((BASE/'AUDIT/SOURCE_AUDIT_SUMMARY.json').read_text('utf-8'))
    twin=json.loads((BASE/'TWIN_PREFLIGHT/RESULT.json').read_text('utf-8'))
    plans={}
    for supplier in SOURCES:
        body=(BASE/'MANIFESTS'/(supplier+'.json')).read_bytes()
        manifest=json.loads(body)
        candidates=[r['canonical_url'] for r in manifest['candidates']]
        known=[e['final_url'] for e in audit['ledger'] if e['source']==supplier and e['label'].startswith('sample:')
               and e['label'] not in {'sample:home','sample:category_radial_drilling_operator_example'}]
        if supplier=='ltt_yug':
            known=[r['final_url'] for r in twin['rows']]+known
        if supplier=='uss_stanko':
            for suffix in ('/mach/sver/b13f.htm','/mach/sver/b17pro.htm','/mach/instrument/instr_rezets_16.htm','/derevo/lathes/instrument.htm'):
                known.extend(u for u in candidates if u.endswith(suffix))
        selected=list(dict.fromkeys(u for u in known if u in candidates))
        groups={}
        for url in candidates:
            if url not in selected and not url.endswith('/index.htm'):
                groups.setdefault(urlparse(url).path.rsplit('/',1)[0],[]).append(url)
        # Round-robin across path groups; no new discovery or model-based dedupe.
        while len(selected)<20:
            grew=False
            for category in sorted(groups):
                if groups[category] and len(selected)<20:
                    selected.append(groups[category].pop(0));grew=True
            if not grew:
                break
        assert len(selected)==20
        plans[supplier]=dict(manifest_sha256=hashlib.sha256(body).hexdigest(),urls=selected)
    return plans


def verify_persisted(repo,product,capture):
    row=repo.connection.execute("""SELECT p.id,o.id,o.price,o.old_price,o.currency,o.availability_normalized,
        p.raw_data,b.price_state,b.price,b.availability_normalized,c.response_sha256,c.evidence_sha256,s.enabled,o.active
        FROM source_products p JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
        JOIN supplier_http_captures c ON c.source_product_id=p.id AND c.capture_fingerprint=%s
        JOIN offer_commercial_observations b ON b.capture_id=c.id AND b.offer_id=o.id
        WHERE s.code=%s AND p.external_id=%s""",(capture.fingerprint,product.supplier,product.site_internal_id)).fetchall()
    assert len(row)==1
    r=row[0]
    assert r[2]==product.price.current_price and r[3]==product.price.old_price
    assert (r[4] or '')==(product.price.currency or '') and r[5]==normalize_availability(product.availability)
    assert r[6]['source_identity']['site_internal_id']==product.site_internal_id
    assert r[6]['source_identity']['raw_model']==(product.raw_supplier_model or product.supplier_model or '')
    assert r[6]['enrichment_evidence']==product.enrichment_evidence
    assert r[7]==product.price.state.value and r[8]==product.price.current_price and r[9]==r[5]
    assert r[10].strip()==capture.response_sha256 and r[11].strip()==capture.evidence_sha256
    assert r[12] is False and r[13] is False
    return dict(source_product_id=r[0],offer_id=r[1],price=str(r[2]) if r[2] is not None else None,availability=r[5])


def gate(rows,supplier):
    successful=[r for r in rows if r['outcome']=='PERSISTED']
    issues=[]
    terminal_names=('PERSISTED','NON_PRODUCT_CATEGORY_PAGE','AMBIGUOUS','PARSE_ERROR','HTTP_STATUS','NETWORK_ERROR','SOURCE_BLOCKED')
    accounting={name:sum(r['outcome']==name for r in rows) for name in terminal_names}
    if sum(accounting.values())!=len(rows):issues.append('UNEXPLAINED_TERMINAL_ROW')
    if len(rows)!=20:issues.append('BOUNDED_SAMPLE_INCOMPLETE')
    if len(successful)<2:issues.append('INSUFFICIENT_UNAMBIGUOUS_PRODUCTS')
    if any(r['outcome'] in {'SOURCE_BLOCKED','PARSE_ERROR','NETWORK_ERROR','HTTP_STATUS'} for r in rows):
        issues.append('CANARY_TERMINAL_FAILURE')
    coverage=dict(numeric=sum(r.get('price_state')=='numeric_public' for r in rows),
                  por_or_missing=sum(r.get('price_state') in {'price_on_request','missing'} for r in rows),
                  available=sum(r.get('availability')=='InStock' for r in rows),
                  unavailable=sum(r.get('availability')=='OutOfStock' for r in rows),
                  non_machine=sum(r.get('kind') in {'spare','tooling','accessory','component'} or
                                  (r.get('kind') in TAXON_BY_KIND and TAXON_BY_KIND[r['kind']].family in ACCESSORY_FAMILIES)
                                  for r in rows),
                  discontinued=sum('uss_discontinued_wording_overrides_stock' in r.get('parser_diagnostics',[]) for r in rows),
                  multi_variant=sum('MULTI_VARIANT' in r.get('capture_diagnostics',[]) for r in rows))
    for key in ('numeric','por_or_missing','available','unavailable','non_machine'):
        if not coverage[key]:issues.append('COVERAGE_MISSING:'+key)
    if supplier=='uss_stanko':
        for key in ('discontinued','multi_variant'):
            if not coverage[key]:issues.append('COVERAGE_MISSING:'+key)
    if supplier=='ltt_yug' and not {'312','297'}.issubset({r.get('external_id') for r in successful}):
        issues.append('YUG_REGRESSION_IDENTITIES_MISSING')
    return dict(status='LIVE_CANARY_PASS' if not issues else 'LIVE_CANARY_BLOCKED',issues=issues,coverage=coverage,persisted=len(successful),terminal_accounting=accounting)


def main():
    OUT.mkdir(exist_ok=True)
    ledger_path=OUT/'LIVE_CANARY.json'
    plans=plan()
    config=json.loads((BASE/'PRIVATE/connection.json').read_text('utf-8'))['runtime']
    assert (config['host'],config['port'],config['dbname'])==('127.0.0.1',55455,'universal_supplier_ltt_uss')
    state=dict(started_at=datetime.now(timezone.utc).isoformat(),status='RUNNING',plans=plans,sources={},rows=[],supplier_gets=0,
               run1_started=False,run2_started=False,full_qa_started=False)
    if ledger_path.exists():
        state=json.loads(ledger_path.read_text('utf-8'))
        if state.get('transition') == 'OPERATOR_CONFIRMED_YUG_CATEGORY_PENDING_ONLY':
            # Original bounded sample is retained, including its explicit category
            # terminal row; only manifest provenance changes, not sample selection.
            for supplier in SOURCES:
                body=(BASE/'MANIFESTS'/(supplier+'.json')).read_bytes()
                assert state['plans'][supplier]['manifest_sha256']==hashlib.sha256(body).hexdigest()
            plans=state['plans']
        else:
            assert state['plans']==plans
        assert state['status']=='RUNNING'
    repo=PostgresRepository(PostgresConfig(**config))
    repo.connection.execute("SET statement_timeout='20s'")
    repo.connection.execute("SET lock_timeout='5s'")
    repo.connection.execute("SET application_name='ltt_uss_live_canary'")
    before=fingerprints(repo.connection)
    save(OUT/'DB_BEFORE_LIVE_CANARY.json',before)
    pacer=PublicPacer(20)
    try:
        for supplier in SOURCES:
            completed={r['requested_url'] for r in state['rows'] if r['supplier']==supplier}
            own=[r for r in state['rows'] if r['supplier']==supplier]
            if supplier in state['sources']:
                continue
            for url in plans[supplier]['urls']:
                if url in completed:continue
                if own and own[-1]['outcome']=='SOURCE_BLOCKED':break
                pacer.before_get()
                started=time.monotonic()
                result=capture_source_html(url,supplier=supplier,evidence_dir=OUT/'EVIDENCE',timeout_seconds=25)
                state['supplier_gets']+=1
                row=dict(supplier=supplier,requested_url=url,final_url=result.final_url,http_status=result.http_status,
                         observed_at=result.observed_at.isoformat(),response_sha256=result.response_sha256,evidence_sha256=result.evidence_sha256,
                         evidence_ref=result.evidence_ref,charset=result.charset,capture_diagnostics=list(result.diagnostics),
                         transport_url=result.transport_url,
                         response_seconds=round(time.monotonic()-started,3))
                outcome={CaptureStatus.BLOCKED:'SOURCE_BLOCKED',CaptureStatus.HTTP_STATUS:'HTTP_STATUS',
                         CaptureStatus.NETWORK_ERROR:'NETWORK_ERROR',CaptureStatus.AMBIGUOUS:'AMBIGUOUS',
                         CaptureStatus.NON_PRODUCT_CATEGORY_PAGE:'NON_PRODUCT_CATEGORY_PAGE'}.get(result.status,'PARSE_ERROR')
                try:
                    if result.status==CaptureStatus.AMBIGUOUS and result.evidence_body:
                        review=parse(supplier,result.evidence_body,result.final_url)
                        row.update(title=review.name,model=review.supplier_model,price_state=review.price.state.value,
                                   availability=review.availability,category=review.source_category,
                                   kind=review.enrichment_evidence['product_kind']['product_kind'],
                                   parser_diagnostics=list(review.diagnostics),nonpersisted_metadata_only=True)
                    if result.status==CaptureStatus.SUCCESS:
                        product=parse(supplier,result.evidence_body,result.final_url)
                        assert product.supplier==supplier and product.name and product.site_internal_id
                        assert product.source_url and product.source_category
                        assert product.enrichment_evidence is not None
                        if product.price.state.value=='numeric_public':assert product.price.currency=='RUB' and product.price.current_price>0
                        else:assert product.price.current_price is None
                        known=repo.connection.execute("SELECT p.canonical_url FROM source_products p JOIN suppliers s ON s.id=p.supplier_id WHERE s.code=%s AND p.external_id=%s",(supplier,product.site_internal_id)).fetchone()
                        if known and known[0]!=product.source_url:
                            row['source_alias_evidence']=dict(stable_id=product.site_internal_id,previous_url=known[0],current_url=product.source_url)
                        repo.persist_commercial_observation(product,result.capture)
                        row.update(external_id=product.site_internal_id,title=product.name,model=product.supplier_model,
                                   brand=product.source_brand or None,price_state=product.price.state.value,
                                   price=str(product.price.current_price) if product.price.current_price is not None else None,
                                   price_raw=product.price.raw_text,availability=product.availability,
                                   kind=product.enrichment_evidence['product_kind']['product_kind'],category=product.source_category,
                                   parser_diagnostics=list(product.diagnostics),capture=asdict(result.capture),
                                   db_verification=verify_persisted(repo,product,result.capture))
                        outcome='PERSISTED'
                except Exception as error:
                    outcome='PARSE_ERROR'
                    row['error']=type(error).__name__+':'+str(error)
                row['outcome']=outcome
                own.append(row);state['rows'].append(row)
                save(ledger_path,state)
                print(json.dumps(dict(source=supplier,completed=len(own),outcome=outcome,id=row.get('external_id'),price=row.get('price'),error=row.get('error')),ensure_ascii=True),flush=True)
                if outcome in {'SOURCE_BLOCKED','PARSE_ERROR','NETWORK_ERROR','HTTP_STATUS'}:
                    break
            state['sources'][supplier]=gate(own,supplier)
            save(ledger_path,state)
            print(json.dumps(dict(source=supplier,gate=state['sources'][supplier]),ensure_ascii=True),flush=True)
            if state['sources'][supplier]['status']!='LIVE_CANARY_PASS':
                state.update(source=supplier,stage='LIVE_CANARY',blocker=state['sources'][supplier]['issues'])
                break
        after=fingerprints(repo.connection)
        protected={name for name in before if name not in ALLOWED}
        changed=[name for name in protected if before[name]!=after[name]]
        assert not changed
        duplicates=repo.connection.execute('SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1').fetchall()
        assert not duplicates
        assert repo.connection.execute("SELECT count(*) FROM source_products p JOIN suppliers s ON s.id=p.supplier_id WHERE s.code='ltt_yug' AND p.external_id IN ('312','297')").fetchone()[0]==2
        state.update(status='LIVE_CANARY_PASS' if len(state['sources'])==4 and all(g['status']=='LIVE_CANARY_PASS' for g in state['sources'].values()) else 'LIVE_CANARY_BLOCKED',
                     finished_at=datetime.now(timezone.utc).isoformat(),protected_table_delta=changed,duplicate_external_ids=0,
                     db_after=after,yug_312_297_retained=True)
        save(ledger_path,state)
        print(json.dumps(dict(status=state['status'],sources=state['sources'],supplier_gets=state['supplier_gets'])),flush=True)
    finally:
        repo.close()
    return 0 if state['status']=='LIVE_CANARY_PASS' else 1


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
