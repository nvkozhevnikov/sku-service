"""Bounded read-only business view; existing normalized/event engine, no ingestion."""
from datetime import datetime,timedelta,timezone
from collections import Counter,defaultdict
from pathlib import Path
from threading import Lock
import json,time,math
from zoneinfo import ZoneInfo
from .store import Store,json_value
from .business_data import normalized_dataset
from .business_report import build_facts,safe_url

LABELS={'PRICE_DECREASE':'Цена снизилась','PRICE_INCREASE':'Цена выросла','NEW_PRODUCT_OBSERVED':'Новый товар','NEW_CAMPAIGN_PRODUCT':'Новый товар в акции','PRODUCT_DISAPPEARED_FROM_CURRENT_CAMPAIGN':'Товар исчез из акции','CAMPAIGN_PRODUCT_REMOVED':'Товар удалён из акции','NEW_PROMOTION':'Новая акция','PROMOTION_CHANGED':'Акция изменилась','PROMOTION_ENDING_SOON':'Акция скоро заканчивается','PROMOTION_ENDED':'Акция завершена','NEW_NEWS':'Новость','NEWS_CHANGED':'Материал обновился','PRICE_REVIEW':'Цена требует проверки','PRICE_SEMANTICS_CHANGED':'Изменились условия цены'}
TYPES={'down':{'PRICE_DECREASE'},'up':{'PRICE_INCREASE'},'new':{'NEW_PRODUCT_OBSERVED','NEW_CAMPAIGN_PRODUCT'},'promotion':{'NEW_PROMOTION','PROMOTION_CHANGED','PROMOTION_ENDING_SOON','PROMOTION_ENDED'},'news':{'NEW_NEWS','NEWS_CHANGED'}}

class MonitoringUnavailable(Exception):pass

def stamp(value):
    return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Moscow')) if value else None

def money_display(value,currency=None):
    if value is None:return '—'
    try:number=f'{float(value):,.2f}'.replace(',',' ').rstrip('0').rstrip('.')
    except (ValueError,TypeError):return '—'
    return number+' '+{'RUB':'₽','USD':'$','EUR':'€','CNY':'¥'}.get(currency,currency if currency and currency!='UNKNOWN' else '(валюта не указана)')

class MonitoringRepository:
    def __init__(self,config):self.config=Path(config)
    def load(self):
        try:
            store=Store(json.loads(self.config.read_text(encoding='utf-8'))['dsn']);store.conn.read_only=True
            try:
                with store.conn.transaction(),store.conn.cursor() as c:
                    c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    def rows(sql,args=(),cap=20000):
                        c.execute(sql,args);result=c.fetchall()
                        if len(result)>cap:raise MonitoringUnavailable('READ_BOUND_EXCEEDED')
                        return json_value(result)
                    sources=rows('SELECT id,code,name,base_url,region,enabled FROM competitor_sources ORDER BY id',cap=30)
                    pages=rows('SELECT id,source_id,canonical_url,title,page_type,current_content_hash,last_seen_at,first_seen_at FROM competitor_pages ORDER BY id LIMIT 1001',cap=1000)
                    versions=rows('''SELECT v.id,v.page_id,v.content_hash,v.extractor_version,v.fetched_at,v.qa_status,
                      jsonb_build_object('title',v.title,'page_type',v.payload->>'page_type','published_at',v.payload->>'published_at',
                      'valid_from',v.payload->>'valid_from','valid_to',v.payload->>'valid_to','normalized_text',v.normalized_text,
                      'commercial_mechanics',v.payload->'commercial_mechanics','campaign_evidence',v.payload->'campaign_evidence',
                      'capture_provenance',v.payload->'capture_provenance','item_scope_complete',v.payload->'item_scope_complete',
                      'source_discovery_complete',v.payload->'source_discovery_complete','scope_key',v.payload->'scope_key') payload
                      FROM competitor_page_versions v JOIN competitor_pages p ON p.id=v.page_id
                      JOIN competitor_page_versions cv ON cv.page_id=p.id AND cv.content_hash=p.current_content_hash
                      WHERE v.qa_status='ACCEPTED' AND v.extractor_version=cv.extractor_version ORDER BY v.id LIMIT 1001''',cap=1000)
                    ids=[v['id'] for v in versions]
                    items=rows('''SELECT id,page_version_id,product_name,product_url,brand,model,category,old_price,new_price,currency,
                      price_type,price_label_raw,availability,region,priority,campaign_name,tab_title FROM competitor_campaign_items
                      WHERE page_version_id=ANY(%s) ORDER BY id LIMIT 20001''',(ids,),cap=20000)
                    observations=rows('''SELECT id,page_id,page_version_id,observed_at,transport FROM competitor_observations
                      WHERE page_version_id=ANY(%s) ORDER BY id LIMIT 5001''',(ids,),cap=5000)
                    surfaces=rows('''SELECT source_id,canonical_url,status,fetch_status,reason,observed_at,robots_disallowed,
                      details->'operator_capture' operator_capture,details->'automated_access_evidence' automated_access_evidence
                      FROM competitor_source_surfaces WHERE status IN ('ERROR','OPERATOR_CAPTURED') ORDER BY id LIMIT 501''',cap=500)
                    for r in surfaces:r['details']={'operator_capture':r.pop('operator_capture'),'automated_access_evidence':r.pop('automated_access_evidence')}
                    c.execute("SELECT to_regclass('public.competitor_operator_captures') name")
                    captures=rows('''SELECT DISTINCT ON(source_id,canonical_url) id,source_id,page_version_id,capture_time,capture_method,evidence_class
                      FROM competitor_operator_captures ORDER BY source_id,canonical_url,capture_time DESC LIMIT 501''',cap=500) if c.fetchone()['name'] else []
                    # Aggregate lookup count only; never read historical seed payload.
                    seeds=rows('SELECT source_id,count(*) n FROM competitor_historical_seeds GROUP BY source_id',cap=30)
                    runs=rows("SELECT id,jsonb_build_object('coverage',result->'coverage') result FROM competitor_runs WHERE result ? 'coverage' ORDER BY id DESC LIMIT 101",cap=100)
                return {'competitor_sources':sources,'competitor_pages':pages,'competitor_page_versions':versions,'competitor_campaign_items':items,'competitor_observations':observations,'competitor_source_surfaces':surfaces,'competitor_operator_captures':captures,'competitor_ai_analysis':[],'competitor_runs':runs,'seed_counts':{r['source_id']:r['n'] for r in seeds}}
            finally:store.close()
        except Exception as error:raise MonitoringUnavailable('COMPETITOR_DATABASE_UNAVAILABLE') from error

def state_facts(state,now):
    source_facts=[];problems={};all_events=[];all_products=[];all_promotions=[];news_offers=[];all_news=[];all_blog=[];sources=[]
    for src in state['competitor_sources']:
        pids={p['id'] for p in state['competitor_pages'] if p['source_id']==src['id']};vids={v['id'] for v in state['competitor_page_versions'] if v['page_id'] in pids}
        subset={'competitor_sources':[src],'competitor_pages':[p for p in state['competitor_pages'] if p['id'] in pids],
          'competitor_page_versions':[v for v in state['competitor_page_versions'] if v['id'] in vids],
          'competitor_campaign_items':[i for i in state['competitor_campaign_items'] if i['page_version_id'] in vids],
          'competitor_observations':[o for o in state['competitor_observations'] if o['page_id'] in pids],
          'competitor_source_surfaces':[r for r in state.get('competitor_source_surfaces',[]) if r['source_id']==src['id']],
          'competitor_operator_captures':[r for r in state.get('competitor_operator_captures',[]) if r['source_id']==src['id']],
          'competitor_ai_analysis':[],'competitor_runs':state.get('competitor_runs',[])}
        try:
            ds=normalized_dataset(subset);f=build_facts(ds,'2026-09-01',str(now.date()))
            s=f['sources'][0];s['pages']=len(ds['current']);s['products']=len({(i.get('product_url') or i['product_name'],i.get('region')) for i in f['products']})
            kinds={p['entity']:p['page_type'] for p in ds['current']}
            all_events.extend(e for e in f['events'] if e['event_type']!='BASELINE_OBSERVATION');all_products+=f['products']
            all_promotions.extend(p for p in f['promotions'] if kinds[p['url']] in ('PROMOTION','CAMPAIGN'))
            news_offers.extend(p for p in f['promotions'] if kinds[p['url']]=='NEWS')
            content_by_url={n['url']:n for n in f['news']+f['blog']}
            for snapshot in ds['current']:
                if snapshot['page_type'] not in ('NEWS','BLOG'):continue
                body=snapshot.get('text','')
                item=content_by_url.get(snapshot['entity']) or {'source':snapshot['source'],'title':snapshot['title'],'url':safe_url(snapshot['entity']),
                    'published_at':snapshot.get('published_at'),'excerpt':' '.join(body.split())[:360],'observed_at':snapshot['observed_at']}
                (all_news if snapshot['page_type']=='NEWS' else all_blog).append({**item,'search_text':body})
            s['comparable']=f['summary']['comparable_entities']>0
        except Exception:
            s={**src,'pages':0,'products':None,'latest_observed_at':None,'comparable':False,'promotions':0};problems[src['code']]='Данные источника требуют проверки';s['data_error']=True
        s['mode']='MANUAL' if src['code']=='youmet' else 'AUTO'
        s['issues']=[r for r in subset['competitor_source_surfaces'] if r['status']=='ERROR']
        for r in subset['competitor_source_surfaces']:
            auto=(r.get('details') or {}).get('automated_access_evidence')
            if auto:s['issues'].append({**r,'fetch_status':auto.get('fetch_status'),'reason':'AUTOMATED_ACCESS_REMAINS_INCOMPLETE'})
        s['status']='MANUAL' if s['mode']=='MANUAL' else 'ERROR' if s.get('data_error') or not s['pages'] and s['issues'] else 'WORKING' if s['pages'] else 'NO_DATA'
        s['status_label']={'MANUAL':'Ручной мониторинг','WORKING':'Работает','ERROR':'Ошибка','NO_DATA':'Данных пока нет'}[s['status']]
        s['last_capture']=max((r['capture_time'] for r in subset['competitor_operator_captures']),default=None)
        observations=[o for o in subset['competitor_observations'] if not o['transport'].startswith(('FIXTURE','OFFLINE'))]
        latest=max(observations,key=lambda o:o['observed_at'],default=None)
        s['capture_method']=latest['transport'] if latest else None
        s['evidence_type']='PUBLIC_BROWSER_EVIDENCE' if s['capture_method']=='OPERATOR_BROWSER_CAPTURE' else 'SAVED_PUBLIC_OBSERVATION' if latest else None
        s['last_event']=max((e.get('observed_after') or e.get('observed_before') for e in all_events if e['source']==s['code']),default=None)
        s['baseline_confirmed']=bool(s['pages']);s['historical_seed_rows']=state.get('seed_counts',{}).get(src['id'],0)
        sources.append(s)
    return {'sources':sources,'events':all_events,'products':all_products,'promotions':all_promotions,'news_offers':news_offers,'news':all_news,'blog':all_blog,'problems':problems}

class MonitoringService:
    def __init__(self,repository,clock=None):self.repository=repository;self.clock=clock or (lambda:datetime.now(ZoneInfo('Europe/Moscow')));self.lock=Lock();self.cached=None;self.expires=0
    def facts(self):
        with self.lock:
            if self.cached is None or time.monotonic()>self.expires:
                self.cached=state_facts(self.repository.load(),self.clock());self.expires=time.monotonic()+30
            return self.cached
    def view(self,tab,query):
        now=self.clock();period=query.get('period','all' if tab=='news' else '7');days={'today':1,'7':7,'30':30}.get(period,7)
        try:
            start=datetime.fromisoformat(query['from']).replace(tzinfo=now.tzinfo) if period=='custom' else (now-timedelta(days=days-1)).replace(hour=0,minute=0,second=0,microsecond=0)
            end=datetime.fromisoformat(query['to']).replace(hour=23,minute=59,second=59,tzinfo=now.tzinfo) if period=='custom' else now
            page=max(1,int(query.get('page',1)))
            if tab=='news' and period=='all':start=datetime(2026,9,1,tzinfo=now.tzinfo)
            if start>end or period=='custom' and (end-start).days>366:raise ValueError()
        except (ValueError,KeyError):raise ValueError('Укажите корректный период (не более года).')
        f=self.facts();names={s['code']:s['name'] for s in f['sources']}
        # Read projection only: reuse stored materials/capabilities and detector events.
        caps={s['code']:set(s.get('capabilities',[])) for s in f['sources']}
        indicators={}
        for event in f['events']:
            if event['event_type'] in ('NEW_NEWS','NEWS_CHANGED'):
                key=(event['source'],event['entity']);when=stamp(event.get('observed_after') or event.get('observed_before'))
                if when and when<=now and (key not in indicators or when>indicators[key][0]):indicators[key]=(when,'NEW' if event['event_type']=='NEW_NEWS' else 'CHANGED')
        def material_time(value):
            if not value:return None
            try:
                parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
                return parsed.replace(tzinfo=now.tzinfo) if parsed.tzinfo is None else parsed.astimezone(now.tzinfo)
            except ValueError:return None
        materials=[]
        for kind,entries in [('NEWS',f['news']),('BLOG',f['blog'])]:
            for item in entries:
                allowed=caps.get(item['source'],set())
                if not (kind in allowed or kind=='BLOG' and allowed & {'ARTICLE','ARTICLES'}):continue
                published=material_time(item.get('published_at'));observed=material_time(item.get('observed_at'));effective=published or observed
                if not effective or effective>now:continue
                signal=indicators.get((item['source'],item['url']))
                materials.append({**item,'source_name':names[item['source']],'content_type':kind,'effective_at':effective,
                    'indicator':signal[1] if signal and kind=='NEWS' else None,'indicator_at':signal[0] if signal and kind=='NEWS' else None})
        materials.sort(key=lambda n:(n['effective_at'],material_time(n.get('observed_at')) or n['effective_at'],n['url']),reverse=True)
        if tab=='news' and period=='all':start=min((n['effective_at'] for n in materials),default=now)
        def matches(row):return (not query.get('source') or row['source']==query['source']) and (not query.get('brand') or row.get('brand')==query['brand']) and (not query.get('search') or query['search'].casefold() in (row.get('product_name') or row.get('title') or '').casefold())
        events=[]
        for e in f['events']:
            when=stamp(e.get('observed_after') or e.get('observed_before'))
            if not when or not start<=when<=end or not matches(e) or query.get('type') in TYPES and e['event_type'] not in TYPES[query['type']]:continue
            pct=abs(float(e.get('percent_delta') or 0));severity='HIGH' if e['event_type']=='PRICE_DECREASE' and pct>=10 or e['event_type']=='NEW_PROMOTION' and e.get('priority')=='HIGH' else 'MEDIUM'
            events.append({**e,'old_observed_price':e.get('old_observed_price'),'new_observed_price':e.get('new_observed_price'),'absolute_delta':e.get('absolute_delta'),'currency':e.get('currency'),'label':LABELS.get(e['event_type'],'Обновление наблюдения'),'when':when,'source_name':names[e['source']],'severity':severity,'severity_reason':'Снижение не менее 10%' if severity=='HIGH' and e['event_type']=='PRICE_DECREASE' else 'Акция промышленного оборудования' if severity=='HIGH' else 'Наблюдаемое изменение источника'})
        events.sort(key=lambda e:e['when'],reverse=True)
        counts=Counter(e['event_type'] for e in events);attention=sorted(events,key=lambda e:(e['severity']!='HIGH',-e['when'].timestamp()))[:6]
        for p in f['promotions']:
            if p['status']=='ENDING_SOON':attention.append({'label':'Акция скоро заканчивается','title':p['title'],'source_name':names[p['source']],'severity':'MEDIUM','severity_reason':'Известная дата окончания: '+str(p['valid_to']),'entity':p['url']})
        for p in f['news_offers']:
            if p['status']=='ENDING_SOON':attention.append({'label':'Срок предложения из новости','title':p['title'],'source_name':names[p['source']],'severity':'MEDIUM','severity_reason':'Объявлено окончание предложения: '+str(p['valid_to']),'entity':p['url']})
        for s in f['sources']:
            if s['mode']=='MANUAL' and not s['last_capture']:attention.append({'label':'Нужен ручной снимок','title':s['name'],'severity':'MEDIUM','severity_reason':'Первый публичный HTML ещё не предоставлен','entity':None})
            if s['status']=='ERROR':attention.append({'label':'Данные источника недоступны','title':s['name'],'severity':'CRITICAL','severity_reason':'Нет принятого снимка или ошибка чтения данных','entity':None})
        rows=events if tab=='changes' else [e for e in events if e['event_type'] in ('PRICE_DECREASE','PRICE_INCREASE')] if tab=='prices' else [p for p in f['promotions'] if matches(p)] if tab=='promotions' else []
        if tab=='news':
            kind=query.get('content_type','ALL').upper()
            if kind not in ('ALL','NEWS','BLOG'):raise ValueError('Выберите тип материала: все, новости или статьи / блог.')
            term=query.get('search','').casefold()
            rows=[n for n in materials if start<=n['effective_at']<=end and
                (not query.get('source') or n['source']==query['source']) and (kind=='ALL' or n['content_type']==kind) and
                (query.get('only_new')!='1' or n['indicator']=='NEW' and n['indicator_at'] and start<=n['indicator_at']<=end) and
                (not term or term in (n['title']+' '+n.get('search_text',n.get('excerpt',''))).casefold())]
        if tab=='prices':rows.sort(key=lambda e:abs(float(e.get('percent_delta') or 0)),reverse=True)
        if tab=='prices' and query.get('sort')=='amount':rows.sort(key=lambda e:float(e.get('absolute_delta') or 0),reverse=True)
        if tab=='prices' and query.get('sort')=='fresh':rows.sort(key=lambda e:e['when'],reverse=True)
        total=len(rows);pages=max(1,math.ceil(total/50));page=min(page,pages)
        source_cards=[]
        for s in f['sources']:
            own=[e for e in events if e['source']==s['code']];source_cards.append({**s,'changes_today':sum(e['when'].date()==now.date() for e in own),'new_promotions':sum(e['event_type']=='NEW_PROMOTION' for e in own)})
        matrix=defaultdict(lambda:defaultdict(set))
        for i in f['products']:matrix[i.get('brand') or 'Бренд не указан'][i['source']].add((i.get('product_url') or i['product_name'],i.get('region')))
        assortment=[{'brand':brand,'counts':{s['code']:len(values.get(s['code'],set())) if s['baseline_confirmed'] else None for s in source_cards}} for brand,values in sorted(matrix.items())]
        if tab=='assortment':
            total=len(assortment);pages=max(1,math.ceil(total/50));page=min(page,pages)
        return {'tab':tab,'now':now,'start':start,'end':end,'query':dict(query),'sources':source_cards,'attention':attention[:8],'rows':rows[(page-1)*50:page*50],'total':total,'page':page,'pages':pages,'brands':sorted({i['brand'] for i in f['products'] if i.get('brand')}),'assortment':assortment[(page-1)*50:page*50],
          'news':[n for n in f['news'] if n.get('published_at') and start.date()<=datetime.fromisoformat(n['published_at']).date()<=end.date()],'blog':[n for n in f['blog'] if n.get('published_at') and start.date()<=datetime.fromisoformat(n['published_at']).date()<=end.date()],
          'materials':materials[:8],'content_sources':[s for s in source_cards if caps.get(s['code'],set()) & {'NEWS','BLOG','ARTICLES'}],
          'last_update':max((s.get('latest_observed_at') for s in source_cards if s.get('latest_observed_at')),default=None),'baseline_only':not any(s['comparable'] for s in source_cards),'kpi':{'prices':counts['PRICE_DECREASE']+counts['PRICE_INCREASE'],'down':counts['PRICE_DECREASE'],'new_products':counts['NEW_PRODUCT_OBSERVED'],'new_promotions':counts['NEW_PROMOTION'],'new_news':len({(e['source'],e['entity']) for e in events if e['event_type']=='NEW_NEWS' and any(n['content_type']=='NEWS' and n['source']==e['source'] and n['url']==e['entity'] for n in materials)}),'attention':len(attention),'errors':sum(s['status']=='ERROR' for s in source_cards)},
          'events_24h':sum(stamp(e.get('observed_after') or e.get('observed_before'))>=now-timedelta(hours=24) for e in f['events']),'events_7d':sum(stamp(e.get('observed_after') or e.get('observed_before'))>=now-timedelta(days=7) for e in f['events'])}
