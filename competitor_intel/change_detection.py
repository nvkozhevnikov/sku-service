"""Pure generic deterministic events. No source-name branches, IO or AI."""
from datetime import date,datetime,timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import hashlib
import json
import re

PUBLIC_TYPES={'PUBLIC','SALE'}
PROMO_TYPES={'PROMOTION','CAMPAIGN'}
INDEX_TYPES={'PROMOTION_INDEX','NEWS_INDEX'}
EVENT_TYPES={'BASELINE_OBSERVATION','NEW_PROMOTION','PROMOTION_CHANGED','PROMOTION_ENDED','PROMOTION_ENDING_SOON',
             'NEW_NEWS','NEWS_CHANGED','NEW_CAMPAIGN_PRODUCT','CAMPAIGN_PRODUCT_REMOVED','PRICE_DECREASE','PRICE_INCREASE',
             'OLD_PRICE_APPEARED','OLD_PRICE_REMOVED','DISCOUNT_CHANGED','NEW_PRODUCT_OBSERVED',
             'PRODUCT_DISAPPEARED_FROM_CURRENT_CAMPAIGN','HOMEPAGE_PROMO_APPEARED','HOMEPAGE_PROMO_REMOVED','PRICE_SEMANTICS_CHANGED','PRICE_REVIEW'}

def decimal(value):
    if value is None or value=='': return None
    n=Decimal(str(value))
    return n if n.is_finite() else None

def day(value):
    if not value: return None
    if len(str(value))>10:
        stamp=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=ZoneInfo('Europe/Moscow'))
        return stamp.astimezone(ZoneInfo('Europe/Moscow')).date()
    return date.fromisoformat(str(value)[:10])

def instant(value):
    stamp=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=ZoneInfo('Europe/Moscow'))
    return stamp.astimezone(timezone.utc)

def item_identity(item):
    # Product title changes do not create a new product when an explicit URL exists.
    return (item.get('product_url') or 'name:'+re.sub(r'\s+',' ',item['product_name']).strip(),item.get('region') or '',item.get('tab_title') or '')

def index_items(snapshot):
    result={}
    for i in snapshot.get('items',[]):
        key=item_identity(i)+(i.get('campaign_name') or '',) if snapshot['page_type']=='HOMEPAGE_BLOCK' else item_identity(i)
        if key in result and result[key]!=i: raise ValueError('Ambiguous product identity in campaign snapshot')
        result[key]=i
    return result

def event_key(event):
    identity={k:event.get(k) for k in ('source','entity','product_identity','event_type','before_version','after_version','before','after','relevant_values')}
    if event.get('before_version') and event.get('before_version')!=event.get('after_version'):
        identity['observation_transition']=[event.get('observed_before'),event.get('observed_after')]
    return hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()

def rank(event):
    high=event.get('priority')=='HIGH'; t=event['event_type']
    order=0 if high and t=='PRICE_DECREASE' else 1 if high and t=='NEW_PROMOTION' else 2 if t=='PROMOTION_ENDING_SOON' else 3 if high and t in ('NEW_PRODUCT_OBSERVED','NEW_CAMPAIGN_PRODUCT') else 4 if t=='DISCOUNT_CHANGED' else 5 if t=='NEW_NEWS' else 9
    return (order,0 if high else 1,event.get('source') or '',event.get('entity') or '',event.get('product_name') or '',t,event.get('event_id') or '')

def detect_events(dataset,report_date,ending_days=7):
    report_date=day(report_date); events={}; timelines={}; source_earliest={}
    history=sorted(dataset.get('history',[]),key=lambda s:(instant(s['observed_at']),s['source'],s['entity'],s['version']))
    for s in history:
        if day(s['observed_at'])>report_date: continue
        timelines.setdefault((s['source'],s['entity']),[]).append(s)
        source_earliest.setdefault(s['source'],s['observed_at'])
    def emit(kind,before,after,item=None,values=None):
        anchor=after or before
        event={'event_type':kind,'source':anchor['source'],'entity':anchor['entity'],'title':anchor['title'],
               'before_version':before['version'] if before else None,'after_version':after['version'] if after else None,
               'observed_before':before['observed_at'] if before else None,'observed_after':after['observed_at'] if after else None,
               'priority':item.get('priority','NORMAL') if item else ('HIGH' if any(i.get('priority')=='HIGH' for i in anchor.get('items',[])) else 'NORMAL'),
               'product_identity':list(item_identity(item)) if item else None,'product_name':item.get('product_name') if item else None,
               'product_url':item.get('product_url') if item else None,'brand':item.get('brand') if item else None,
               'model':item.get('model') if item else None,'category':item.get('category') if item else None,
               'currency':item.get('currency') if item else None,
               'campaign_context':item.get('campaign_name') if item else anchor['title'],'relevant_values':values or {}}
        if values: event.update({k:v for k,v in values.items() if k in ('before','after','old_observed_price','new_observed_price','absolute_delta','percent_delta','price_type_before','price_type_after')})
        event['event_id']=event_key(event); events[event['event_id']]=event
    for (source,entity),snaps in sorted(timelines.items()):
        previous=None
        for s in snaps:
            reset=bool(previous and previous.get('extractor')!=s.get('extractor'))
            if reset: previous=None
            if previous is None:
                # New page ≠ new campaign unless comparable discovery scope proves absence.
                coverage=next((c for c in dataset.get('coverage',[]) if c['source']==source and c.get('complete') and instant(c['observed_at'])<instant(s['observed_at']) and entity not in c['entities']),None) if not reset else None
                kind='NEW_PROMOTION' if coverage and s['page_type'] in PROMO_TYPES else 'NEW_NEWS' if coverage and s['page_type']=='NEWS' else 'BASELINE_OBSERVATION'
                emit(kind,None,s)
                if coverage:
                    for i in s.get('items',[]):
                        known=any(any(item_identity(old)[:2]==item_identity(i)[:2] for old in other.get('items',[])) for other in history if other['source']==source and instant(other['observed_at'])<instant(s['observed_at']))
                        if not known: emit('NEW_PRODUCT_OBSERVED',None,s,i)
                previous=s; continue
            if previous['version']==s['version']: previous=s; continue
            a,b=index_items(previous),index_items(s)
            fields=('title','valid_from','valid_to','text','mechanics')
            changed=[f for f in fields if previous.get(f)!=s.get(f)]
            prices_changed=any((a[k].get('new_price'),a[k].get('old_price'),a[k].get('price_type'))!=(b[k].get('new_price'),b[k].get('old_price'),b[k].get('price_type')) for k in set(a)&set(b))
            members_comparable=previous.get('item_scope_complete') and s.get('item_scope_complete') and previous.get('scope_key')==s.get('scope_key')
            if s['page_type'] in PROMO_TYPES and (changed or members_comparable and set(a)!=set(b) or prices_changed):
                emit('PROMOTION_CHANGED',previous,s,values={'changed_fields':changed,'added':len(set(b)-set(a)),'removed_observed':len(set(a)-set(b)),
                                                         'scope_complete':s.get('item_scope_complete',False),'end_date_before':previous.get('valid_to'),'end_date_after':s.get('valid_to')})
            if s['page_type']=='NEWS' and changed: emit('NEWS_CHANGED',previous,s,values={'changed_fields':changed})
            for key in sorted(set(b)-set(a)):
                if members_comparable:
                    emit('NEW_CAMPAIGN_PRODUCT',previous,s,b[key])
                    product_key=item_identity(b[key])[:2]
                    if not any(any(item_identity(i)[:2]==product_key for i in other.get('items',[])) for other in history if other['source']==source and instant(other['observed_at'])<instant(s['observed_at'])): emit('NEW_PRODUCT_OBSERVED',previous,s,b[key])
            for key in sorted(set(a)-set(b)):
                if members_comparable:
                    emit('CAMPAIGN_PRODUCT_REMOVED',previous,s,a[key]); emit('PRODUCT_DISAPPEARED_FROM_CURRENT_CAMPAIGN',previous,s,a[key])
            if s['page_type']=='HOMEPAGE_BLOCK' and members_comparable:
                old_blocks={i.get('campaign_name') for i in a.values() if i.get('campaign_name')}; new_blocks={i.get('campaign_name') for i in b.values() if i.get('campaign_name')}
                for block in sorted(new_blocks-old_blocks): emit('HOMEPAGE_PROMO_APPEARED',previous,s,values={'block_title':block})
                for block in sorted(old_blocks-new_blocks): emit('HOMEPAGE_PROMO_REMOVED',previous,s,values={'block_title':block})
            for key in sorted(set(a)&set(b)):
                old,new=a[key],b[key]; ot,nt=old.get('price_type','UNKNOWN'),new.get('price_type','UNKNOWN')
                op,np=decimal(old.get('new_price')),decimal(new.get('new_price'))
                comparable=ot in PUBLIC_TYPES and nt in PUBLIC_TYPES and old.get('currency','RUB')==new.get('currency','RUB') and old.get('region')==new.get('region')
                values={'price_type_before':ot,'price_type_after':nt,'before':old.get('new_price'),'after':new.get('new_price')}
                if not comparable:
                    if ot!=nt or old.get('currency','RUB')!=new.get('currency','RUB') or old.get('region')!=new.get('region'): emit('PRICE_SEMANTICS_CHANGED',previous,s,new,values)
                    elif op!=np: emit('PRICE_REVIEW',previous,s,new,values)
                    continue
                if op is not None and np is not None and op>0 and np>0 and op!=np:
                    values.update(old_observed_price=op,new_observed_price=np,absolute_delta=abs(np-op),percent_delta=((np-op)*100/op).quantize(Decimal('.01')))
                    emit('PRICE_DECREASE' if np<op else 'PRICE_INCREASE',previous,s,new,values)
                oo,no=decimal(old.get('old_price')),decimal(new.get('old_price'))
                if oo is None and no is not None: emit('OLD_PRICE_APPEARED',previous,s,new,{'before':None,'after':no})
                if oo is not None and no is None: emit('OLD_PRICE_REMOVED',previous,s,new,{'before':oo,'after':None})
                def discount(i):
                    oldp,cur=decimal(i.get('old_price')),decimal(i.get('new_price'))
                    return ((oldp-cur)*100/oldp).quantize(Decimal('.01')) if oldp and cur is not None and 0<cur<oldp else None
                if discount(old)!=discount(new): emit('DISCOUNT_CHANGED',previous,s,new,{'before':discount(old),'after':discount(new)})
            previous=s
        last=snaps[-1]; end=day(last.get('valid_to'))
        if last['page_type'] in PROMO_TYPES and end:
            remaining=(end-report_date).days
            if 0<=remaining<=ending_days: emit('PROMOTION_ENDING_SOON',last,last,values={'valid_to':str(end),'days_remaining':remaining,'urgency':'3_DAYS' if remaining<=3 else '7_DAYS'})
            # Date proves expiration; event requires an earlier still-active observation.
            active=next((s for s in snaps if day(s['observed_at'])<=end),None)
            if remaining<0 and active: emit('PROMOTION_ENDED',active,last,values={'valid_to':str(end)})
    return sorted(events.values(),key=rank)
