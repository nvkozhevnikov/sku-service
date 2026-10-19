"""Prepared metadata → separate lookup table; never current pages/items/events."""
import hashlib,json
from pathlib import Path
from psycopg.types.json import Jsonb
from .historical_seed import seed_url,stamp

SEED_LOGICAL_SHA='ba60d3052d2c6a3265719d49b2d9be7bc0ff6b6adb6633dfe87916cebfb56f07'

def verified_seed_bundle(directory):
    p=Path(directory); data=(p/'HISTORICAL_SEEDS.jsonl').read_bytes(); manifest=json.loads((p/'SEED_RESULT.json').read_text(encoding='utf-8'))
    # Original export recorded LF logical bytes, while Windows wrote CRLF.
    # Validate exactly that known encoding difference without rewriting the artifact.
    normalized=data.replace(b'\r\n',b'\n')
    digest=hashlib.sha256(normalized).hexdigest()
    if digest!=manifest['output_sha256'] or digest!=SEED_LOGICAL_SHA: raise ValueError('Seed integrity mismatch; no import')
    rows=[json.loads(line) for line in data.splitlines() if line.strip()]
    if len(rows)!=5264 or len({r['seed_id'] for r in rows})!=5264 or len({r['product_url'] for r in rows})!=5264: raise ValueError('Seed identity/count mismatch')
    for r in rows:
        if r['evidence_class']!='HISTORICAL_SEED' or r['is_live'] is not False or r['live_price_eligible'] or r['new_product_event_eligible']: raise ValueError('Historical eligibility contract violated')
        if any(r.get(k) is not None for k in ('price','old_price','availability')): raise ValueError('Historical commercial facts must remain NULL')
        if seed_url(r['product_url'])!=r['product_url'] or stamp(r.get('original_capture_time')) is None: raise ValueError('Invalid historical URL/time')
    return rows,{'physical_sha256':hashlib.sha256(data).hexdigest(),'logical_sha256':digest,'rows':len(rows),'rebuilt':False,'encoding':'UTF8/CRLF original artifact preserved'}

def import_seed_cache(store,directory):
    rows,proof=verified_seed_bundle(directory)
    with store.conn.transaction(),store.conn.cursor() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS competitor_historical_seeds (
          source_id bigint NOT NULL REFERENCES competitor_sources(id),seed_id char(64) NOT NULL,
          product_url text NOT NULL,source_record_id text NOT NULL,product_name text,brand text,model text,category jsonb NOT NULL,
          original_capture_time timestamptz NOT NULL,provenance text NOT NULL CHECK(provenance='REUSED_KAMI_CAPTURE'),
          evidence_class text NOT NULL CHECK(evidence_class='HISTORICAL_SEED'),is_live boolean NOT NULL CHECK(is_live=false),
          payload jsonb NOT NULL,PRIMARY KEY(source_id,seed_id),UNIQUE(source_id,product_url,source_record_id))''')
        c.execute("INSERT INTO competitor_sources(code,name,base_url,region,enabled) VALUES('stanki_ru','КАМИ / stanki.ru','https://www.stanki.ru/','Россия',true) ON CONFLICT(code) DO NOTHING")
        c.execute("SELECT id FROM competitor_sources WHERE code='stanki_ru'"); sid=c.fetchone()['id']
        inserted=0
        for row in rows:
            c.execute('''INSERT INTO competitor_historical_seeds(source_id,seed_id,product_url,source_record_id,product_name,brand,model,category,original_capture_time,provenance,evidence_class,is_live,payload)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'REUSED_KAMI_CAPTURE','HISTORICAL_SEED',false,%s)
                         ON CONFLICT(source_id,seed_id) DO NOTHING RETURNING seed_id''',
                      (sid,row['seed_id'],row['product_url'],row['source_record_id'],row['product_name'],row.get('brand'),row.get('model'),Jsonb(row['category']),row['original_capture_time'],Jsonb(row)))
            inserted+=int(c.fetchone() is not None)
        c.execute('SELECT count(*) AS n FROM competitor_historical_seeds WHERE source_id=%s',(sid,)); count=c.fetchone()['n']
        if count!=5264: raise ValueError('Cache count mismatch')
    return {**proof,'source':'stanki_ru','inserted':inserted,'cache_rows':count,'current_pages_written':0,'current_prices_written':0,'events_written':0}
