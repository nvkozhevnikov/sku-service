"""Publication checks, not import permission. Identity and history do not expire."""
from decimal import Decimal
from xml.etree import ElementTree as ET
from .export_eligibility import semantic_export_hold

GATE='NO_UNEXPLAINED_DATA_OR_EXPORT_REGRESSION'

def validate_payload(data,manifest):
    if set(data)!= {'updates','new_products'}:raise ValueError('PREVIEW_PAIR_REQUIRED')
    for kind,raw in data.items():
        root=ET.fromstring(raw);nodes=root.findall('product')
        if (root.tag!='sterbrust_preview' or root.get('kind')!=kind or root.get('import_allowed')!='false'
                or root.get('generated_at')!=manifest['generated_at'] or len(nodes)!=manifest['counts'][kind]):
            raise ValueError('PREVIEW_STRUCTURE_OR_COUNT_INVALID')
        ids=[n.get('sterbrust_product_id') if kind=='updates' else n.get('candidate_id') for n in nodes]
        if any(not i for i in ids) or len(set(ids))!=len(ids):raise ValueError('PREVIEW_IDENTITY_INVALID')
        traces={str(t['sterbrust_product_id']):t for t in manifest.get('selection_trace',[])}
        selected={str(t['source_product_id']):t for t in manifest.get('record_trace',{}).get('new_products',[]) if t['selected']}
        for node in nodes:
            if kind=='updates':
                trace=traces.get(node.get('sterbrust_product_id'))
                if not trace or any(c.tag not in ('price','currency','availability') for c in node):raise ValueError('PREVIEW_UPDATE_IDENTITY_OR_FIELD')
                if semantic_export_hold(trace):raise ValueError('PREVIEW_SEMANTIC_EXPORT_HOLD')
                freshness=trace['freshness'];price=node.find('price');stock=node.find('availability')
                if price is None and stock is None:raise ValueError('PREVIEW_EMPTY_UPDATE')
                if price is not None and (freshness['price']['state']!='fresh' or Decimal(price.text)<=0 or not node.findtext('currency')):raise ValueError('PREVIEW_STALE_PRICE')
                if stock is not None and freshness['availability']['state']!='fresh':raise ValueError('PREVIEW_STALE_AVAILABILITY')
            else:
                trace=selected.get(node.get('source_product_id'))
                if not trace or trace.get('candidate_id')!=node.get('candidate_id'):raise ValueError('PREVIEW_NEW_IDENTITY_INVALID')
                fresh=manifest['freshness'].get(trace['offer_id'],manifest['freshness'].get(str(trace['offer_id']),{}))
                if fresh.get('content',{}).get('state')!='fresh':raise ValueError('PREVIEW_STALE_NEW_CONTENT')
                for tag,field in [('price','price'),('availability_normalized','availability')]:
                    if node.find('commercial/'+tag) is not None and fresh[field]['state']!='fresh':raise ValueError('PREVIEW_STALE_NEW_FIELD')

def compare(previous,current):
    errors=[];explanations=[]
    if previous:
        before=previous.get('data_inventory');after=current.get('data_inventory')
        if before:
            if not after:errors.append('DATA_INVENTORY_MISSING')
            else:
                for key in ('source_products','offers','commercial_observations','http_captures'):
                    if after[key]<before[key]:errors.append('SAVED_ROWS_DECREASED:'+key)
                for key in ('suppliers','canonical_links','source_identities','offer_identities','observation_identities','capture_identities'):
                    if key in before and not {tuple(r) for r in before[key]}<={tuple(r) for r in after.get(key,[])}:errors.append('DURABLE_IDENTITY_LOST:'+key)
        for kind in ('updates','new_products'):
            old=previous.get('included_sources',{}).get(kind,[])
            rows={r['source_product_id']:r for r in current.get('record_trace',{}).get(kind,[])}
            for source_id in old:
                row=rows.get(source_id)
                if row is None or not row['selected'] and not row['reasons']:
                    errors.append(f'UNEXPLAINED_EXPORT_LOSS:{kind}:{source_id}')
                elif not row['selected']:explanations.append({'kind':kind,'source_product_id':source_id,'reasons':row['reasons'],'warnings':row.get('warnings',[])})
    return {'name':GATE,'status':'BLOCKED' if errors else 'PASS','errors':errors,'explained_exclusions':explanations,'import_allowed':False}
