"""Real PostgreSQL fixture import in a rollback-only schema; no real captures."""
import json,hashlib,uuid
from pathlib import Path
from psycopg import sql
from competitor_intel.store import Store,json_value
from competitor_intel.operator_capture import import_browser_capture
from competitor_intel.business_data import read_state,fingerprints,normalized_dataset,TABLES
from competitor_intel.business_report import build_facts,export_preview

root=Path('reports/YOUMET_OPERATOR_CAPTURE_SUPPORT_2026-10-12');root.mkdir(exist_ok=True)
inputs=root/'SYNTHETIC_INPUTS';inputs.mkdir(exist_ok=True)
out=root/'FIXTURE_CAPTURE_OUTPUTS';out.mkdir(exist_ok=True)
card='''<div class="catalog_item"><div class="item-title"><a href="/shop/fixture-lathe/">Токарный станок FIXTURE</a></div><div class="price" data-currency="RUB"><span class="price_value">80 000</span><span class="price_currency"> ₽</span></div><div class="price discount"><span class="price_value">100 000</span></div><div class="item-stock"><span class="value">В наличии</span></div></div>'''
body='<main><h1>Синтетическая акция FIXTURE — не реальные данные YouMet</h1><time datetime="2026-10-01">1 октября 2026</time><div class="detail-text">Скидки с 1 по 31 октября 2026.</div>'+card+'</main><script>document.cookie="QA_COOKIE_SECRET";localStorage.access_token="QA_ACCESS_SECRET";</script><input type="password" value="QA_PASSWORD_SECRET">'
file=inputs/'promo.html';file.write_text(body,encoding='utf-8');original=file.read_bytes()
config=json.loads(Path('competitor-data/dev-config.json').read_text());store=Store(config['dsn'])
proof={'evidence_class':'REPOSITORY_VERIFIED_ROLLBACK_FIXTURES_NOT_YOUMET_SOURCE_EVIDENCE','checks':{},'source_HTTP':0,'AI':False,'scheduler':False,'real_operator_captures_supplied':0}
class RollbackFixture(Exception):pass
try:
    before=read_state(store);before_fp=fingerprints(before)
    try:
        with store.conn.transaction():
            schema='competitor_operator_qa_'+uuid.uuid4().hex[:12]
            with store.conn.cursor() as c:
                c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
                c.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
                c.execute(Path('competitor_intel/schema.sql').read_text(encoding='utf-8'))
                c.execute("INSERT INTO competitor_sources(code,name,base_url,region) VALUES('youmet','YouMet — FIXTURE','https://youmet.ru/','Москва')")
                c.execute(Path('competitor_intel/kuvalda_surfaces.sql').read_text(encoding='utf-8'))
                c.execute('''INSERT INTO competitor_source_surfaces(source_id,canonical_url,surface_kind,status,robots_disallowed,fetch_status,evidence_class,reason,observed_at,details)
                  VALUES(1,'https://youmet.ru/akcia/fixture/','PROMOTION','ERROR',true,'HTTP_BLOCKED','TRANSPORT_ERROR','ACTUAL_ACCESS_FAILURE','2026-10-08T12:00:00+03:00','{"fetch_status":"HTTP_BLOCKED","http_status":403}')''')
            first=import_browser_capture(store,file,'https://youmet.ru/akcia/fixture/','2026-10-09T12:00:00+03:00',out)
            proof['checks']['source_input_unchanged_by_import']=file.read_bytes()==original
            proof['checks']['first_version_created']=first['versions']==1
            def state():
                d={}
                with store.conn.cursor() as c:
                    for table in TABLES:
                        c.execute('SELECT to_regclass(%s) AS name',(table,))
                        if c.fetchone()['name'] is None:d[table]=[];continue
                        c.execute('SELECT * FROM '+table);d[table]=json_value(c.fetchall())
                return d
            ds=normalized_dataset(state());facts=build_facts(ds,'2026-09-01','2026-10-09')
            proof['checks']['first_observation_only_baseline']={e['event_type'] for e in facts['events']}=={'BASELINE_OBSERVATION'}
            proof['checks']['operator_capture_provenance_in_dataset']=ds['sources'][0]['capture_methods']==['OPERATOR_BROWSER_CAPTURE']
            proof['checks']['automated_block_not_hidden']=ds['sources'][0]['access_gaps'][0]['fetch_status']=='HTTP_BLOCKED'
            proof['checks']['product_rows_operator_provenance']=facts['products'][0]['capture_method']=='OPERATOR_BROWSER_CAPTURE' and facts['products'][0]['fetch_status']=='OPERATOR_CAPTURE'
            export_preview(ds,'2026-09-01','2026-10-09',root/'FIXTURE_PREVIEW')
            second=import_browser_capture(store,file,'https://youmet.ru/akcia/fixture/','2026-10-09T12:00:00+03:00',out)
            proof['checks']['same_html_no_duplicate_version']=second['versions']==0 and len(store.query('SELECT id FROM competitor_page_versions'))==1
            proof['checks']['same_capture_no_duplicate_ledger']=len(store.query('SELECT id FROM competitor_operator_captures'))==1
            preserved=store.query("SELECT details FROM competitor_source_surfaces WHERE canonical_url='https://youmet.ru/akcia/fixture/'")[0]['details']
            proof['checks']['repeated_capture_preserves_original_403']=preserved['automated_access_evidence']['http_status']==403
            file.write_text(body.replace('80 000','70 000'),encoding='utf-8')
            changed=import_browser_capture(store,file,'https://youmet.ru/akcia/fixture/','2026-10-10T12:00:00+03:00',out)
            proof['checks']['different_html_new_version']=changed['versions']==1 and len(store.query('SELECT id FROM competitor_page_versions'))==2
            regionless=build_facts(normalized_dataset(state()),'2026-09-01','2026-10-10')
            proof['checks']['unknown_regions_not_compared_as_price_change']=not any(e['event_type'] in ('PRICE_DECREASE','PRICE_INCREASE') for e in regionless['events'])
            try:import_browser_capture(store,file,'https://youmet.ru/akcia/fixture/','2026-10-09T12:00:00+03:00',out)
            except ValueError as e:proof['checks']['older_capture_does_not_replace_current']='Older capture' in str(e)
            else:proof['checks']['older_capture_does_not_replace_current']=False
            for path,kind in [('about/news/fixture/','NEWS'),('blog/fixture/','BLOG')]:
                child=inputs/(kind+'.html');child.write_text('<main><h1>Материал '+kind+' FIXTURE</h1><time datetime="2026-10-01">1 октября 2026</time><article>Синтетический публичный текст.</article></main>',encoding='utf-8')
                imported=import_browser_capture(store,child,'https://youmet.ru/'+path,'2026-10-09T12:00:00+03:00',out)
                proof['checks'][kind+'_independent_import']=imported['pages']==1 and imported['versions']==1
            home=inputs/'HOME.html';home.write_text('<h1>Юмет FIXTURE</h1><a href="/akcia/">Акции</a><a href="/about/news/">Новости</a><a href="/blog/">Блог</a>',encoding='utf-8')
            home_result=import_browser_capture(store,home,'https://youmet.ru/','2026-10-09T12:00:00+03:00',out)
            surfaces=store.query('SELECT * FROM competitor_source_surfaces')
            proof['checks']['navigation_discovered_not_fetched']=all(any(r['canonical_url']=='https://youmet.ru/'+p and r['status']=='DISCOVERED_NOT_FETCHED' for r in surfaces) for p in ('akcia/','about/news/','blog/'))
            stored=json.dumps(state(),ensure_ascii=False,default=str)+''.join(p.read_text(encoding='utf-8') for p in out.iterdir())
            proof['checks']['no_secret_values_persisted']=not any(s in stored for s in ('QA_COOKIE_SECRET','QA_ACCESS_SECRET','QA_PASSWORD_SECRET'))
            proof['checks']['BLOG_distinct_from_NEWS']={r['page_type'] for r in store.query('SELECT page_type FROM competitor_pages')} >= {'BLOG','NEWS'}
            raise RollbackFixture()
    except RollbackFixture:pass
    after_fp=fingerprints(read_state(store));proof['checks']['all_actual_source_and_table_fingerprints_equal']=before_fp==after_fp
    proof['checks']['fixture_schema_absent_after_rollback']=store.query('SELECT to_regnamespace(%s) AS schema',(schema,))[0]['schema'] is None
    proof['before']=before_fp;proof['after']=after_fp;proof['fixture_schema_rolled_back']=True
    proof['status']='PASS' if all(proof['checks'].values()) else 'FAIL'
    (root/'IMPORT_QA.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    if proof['status']!='PASS':raise RuntimeError('Operator import fixture QA failed')
    print(json.dumps({'status':proof['status'],'checks':proof['checks'],'real_operator_captures_supplied':0},ensure_ascii=False))
finally:store.close()
