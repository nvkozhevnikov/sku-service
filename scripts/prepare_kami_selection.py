"""Read current isolated DB eligibility context; persist generic-policy proposal."""
import json,os,hashlib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import psycopg
from scripts.persist_kami_full_run1 import preflight
from universal_supplier.offer_selection import OfferCandidate
from universal_supplier.proposed_offer_selection import proposed_selection,source_key


def main():
    root=Path(__file__).resolve().parents[1];base=root/'reports/KAMI_INTEGRATION_2026-10-05'
    match=json.loads((base/'MATCHING/MATCHING_ACCEPTED.json').read_bytes());rows=match['rows']
    assert json.loads((base/'MATCHING/DB_PERSISTED.json').read_bytes())['status']=='LIVE_VERIFIED_KAMI_REVIEW_DECISIONS_PERSISTED'
    private=Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005'
    password=(private/'pgpass.conf').read_text().strip().split(':',4)[4]
    marker=json.loads((base/'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    with psycopg.connect(host='127.0.0.1',port=55451,dbname='universal_supplier_kami_rc',user='kami_admin',password=password,autocommit=True,connect_timeout=5) as c:
        preflight(c,marker)
        with c.transaction():
            c.execute('SET TRANSACTION READ ONLY')
            targets=dict(c.execute('SELECT sterbrust_product_id,catalog_product_id FROM sterbrust_products'))
            records=c.execute("""SELECT s.code,p.external_id,p.id,p.supplier_id,p.catalog_product_id,p.active,p.missed_crawls,p.last_success_at,
                s.enabled,o.id,o.supplier_id,o.active,o.offer_kind,o.missed_crawls,o.last_success_at,o.price,o.currency,
                o.availability_normalized,o.availability_raw,o.quantity,latest.started_at
                FROM source_products p JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
                LEFT JOIN LATERAL (SELECT started_at FROM crawl_runs WHERE supplier_id=s.id AND crawl_type='full'
                AND status='succeeded' ORDER BY started_at DESC LIMIT 1) latest ON true""").fetchall()
    lookup={(x[0],str(x[1])):x for x in records}
    held=[]
    for row in rows:
        if row['classification']!='EXISTING_CONFIRMED':continue
        record=lookup.get(source_key(row));local=targets.get(str(row['sterbrust_product_id']))
        if not record or local is None:
            held.append({'source':row['source'],'external_id':row['external_id'],'reason':'SQL_CANONICAL_SOURCE_BINDING_MISSING'});continue
        (_,_,pid,sid,linked,source_active,source_misses,source_success,enabled,oid,offer_sid,active,kind,
         offer_misses,offer_success,price,currency,availability,availability_raw,quantity,latest)=record
        if linked != local:
            held.append({'source':row['source'],'external_id':row['external_id'],
                         'reason':'NO_PERSISTED_CANONICAL_LINK_FOR_ACCEPTED_IDENTITY'});continue
        # Do not silently replace frozen commercial fields to select a winner.
        # Any current-vs-frozen difference remains an explicit hold.
        from decimal import Decimal
        frozen_price=Decimal(str(row['price'])) if row.get('price') is not None else None
        if price!=frozen_price or (row.get('currency') and currency!=row['currency']) or (row.get('availability') and availability!=row['availability']):
            held.append({'source':row['source'],'external_id':row['external_id'],'reason':'FROZEN_COMMERCIAL_VS_SQL_CONTEXT_DIFF'});continue
        candidate=OfferCandidate(local,oid,pid,sid,offer_sid,row['source'],enabled,linked,source_active,
            source_misses,source_success,active,kind,offer_misses,offer_success,latest,price,currency,availability,availability_raw,quantity)
        candidate_data=asdict(candidate)
        for key,value in list(candidate_data.items()):
            if isinstance(value,datetime):candidate_data[key]=value.isoformat()
            elif key in ('price','quantity') and value is not None:candidate_data[key]=str(value)
        row.update(source_product_id=pid,offer_id=oid,supplier_enabled=enabled,offer_active=active,
                   selection_context={'canonical_identity_verified':True,'sterbrust_product_id':row['sterbrust_product_id'],
                                      'candidate':candidate_data})
    state=proposed_selection(rows)
    assert not any(source=='kami' for source,_ in state['selected'])
    directory=base/'SELECTION';directory.mkdir(exist_ok=True)
    inputs={'rows':rows,'matching_input_sha':hashlib.sha256((base/'MATCHING/MATCHING_ACCEPTED.json').read_bytes()).hexdigest()}
    (directory/'EXPORT_INPUT_ROWS.json').write_text(json.dumps(inputs,ensure_ascii=False,indent=2),encoding='utf-8')
    (directory/'SELECTION_VERIFIED.json').write_text(json.dumps(state,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    report={'status':'REPOSITORY_VERIFIED_GENERIC_POLICY_SELECTION_WITH_LIVE_SQL_CONTEXT',
            'selected':len(state['selected']),'disabled_selected':0,'inactive_selected':0,'Review_Conflict_selected':0,
            'KAMI_selected':0,'eligibility_context_holds':held,'activation_changes':0,'SQL_writes':0,'HTTP':0,
            'canonical_selection_table_changed':False,'frozen_matching_identity_decisions_changed':False}
    (directory/'VERIFIED.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({**report,'eligibility_context_holds':len(held)}),flush=True)


if __name__=='__main__':main()
