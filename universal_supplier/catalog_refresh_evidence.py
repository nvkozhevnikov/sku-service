"""Pure refresh evidence normalization. Does not crawl, ingest or assign IDs."""
from copy import deepcopy
from decimal import Decimal


def commercial_evidence(row, *, hidden_price_only=False):
    result=deepcopy(row)
    state=row['price_state']
    if state not in ('numeric','price_on_request','missing'): raise ValueError('Unknown price state')
    amount=row.get('price_amount')
    if state=='numeric':
        if amount in (None,''): raise ValueError('Numeric price missing')
        price=Decimal(str(amount))
        if not price.is_finite() or price<0: raise ValueError('Invalid visible price')
        result['normalized_price']=format(price,'f')
    else:
        if amount not in (None,''): raise ValueError('Non-numeric state carries an amount')
        result['normalized_price']=None
    hidden=(row.get('alias_evidence') or {}).get('hidden_price')
    result['commercial_primary']='visible_user_facing_listing_or_detail'
    result['hidden_price_metadata']={'value':hidden,'provenance':'secondary_hidden_metadata',
                                     'identity_conflict':False}
    # Only the explicitly commercial hidden-price flag may be separated from
    # identity. Never clear article/model conflicts from a broader payload.
    conflict=row.get('identity_conflict')
    if conflict is True and hidden is not None and hidden_price_only:
        result['identity_conflict']=False
        result['commercial_metadata_conflict']=True
    return result


def compact_identity(row):
    def relevant(prop):
        code=str(prop.get('property_code') or '').upper()
        name=str(prop.get('property_name') or '').casefold()
        return code in {'ARTNUMBER','MODEL','BRAND'} or code.startswith('ARTIKUL') or any(
            token in name for token in ('артикул','модель','исполнен','бренд','производител'))
    props=[{'property_id':str(p.get('property_id') or ''),'property_code':p.get('property_code') or '',
            'property_name':p.get('property_name') or '', 'value_flat':p.get('value_flat') or ''}
           for p in row.get('properties',[]) if isinstance(p,dict) and relevant(p)]
    return {**{k:row.get(k) or '' for k in ('active','name','article_raw','supplier_article_raw','brand_raw',
                'brand_id','model_raw','category_id','category_name','category_path','product_url')},
            'sterbrust_product_id':str(row.get('sterbrust_product_id') or ''),'relevant_properties':props}


def identity_key(row):
    return (row['active'],row['name'],row['article_raw'],row['supplier_article_raw'],row['brand_raw'],
            row['model_raw'],row['category_id'],row['category_path'],
            tuple((p['property_id'],p['value_flat']) for p in row['relevant_properties']))
