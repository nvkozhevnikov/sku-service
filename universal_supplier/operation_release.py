"""Two deterministic neutral operation artifacts, never an ESOL importer.

Operational existing identities/selection must come from persisted SQL.
Readiness is an explicit accepted proof, never inferred from advisory classes.
"""
from decimal import Decimal,InvalidOperation
import hashlib,json
from xml.etree import ElementTree as ET
from .effective_identity import effective_identity_accepted
from .release_export import CONTRACT,compact,identity,validate,trace_id,_json
from .yml_feed import SUPPORTED_PRICE_TYPES
from .xml_export import FALSE_MATCH_GUARDS

PROFILE='operation-split/1.0'


def numeric(value):
    try:
        n=Decimal(str(value))
        return n if n.is_finite() and n>0 else None
    except (InvalidOperation,ValueError):return None


def plan_operations(rows,payloads,current_prices,*,scope=('kami',)):
    """Pure planning: no matching/reselection/current-state fetch side effects."""
    by_payload={identity(p):p for p in payloads};new=[];prices=[];excluded=[]
    seen={};new_groups=set();price_ids=set()
    for r in sorted(rows,key=identity):
        key=identity(r)
        if key in seen:
            if compact(r)!=compact(seen[key]):raise ValueError('Contradictory duplicate source evidence')
            continue
        seen[key]=r
        def reject(reason):excluded.append({'source':key[0],'external_id':key[1],'name':r.get('name',''),'reason':reason,'classification':r['classification']})
        if key[0] not in scope:reject('OUT_OF_SCOPE_PREDECESSOR');continue
        if key in {(s,e) for s,e,_ in FALSE_MATCH_GUARDS} or key[1]=='305':reject('KNOWN_FALSE_MATCH_GUARD');continue
        if r.get('product_kind') in {'accessory','component','controller','handle','jaw','chuck_jaw','part','consumable'}:reject('NON_MACHINE_OPERATION_SCOPE');continue
        cls=r['classification']
        if cls=='READY_TO_CREATE_FULL':
            if r.get('readiness_authority')!='OPERATOR_ACCEPTED_FULL_PROOF':reject('READINESS_ADVISORY_NOT_OPERATION_AUTHORITY');continue
            if r.get('sterbrust_product_id') or r.get('source_catalog_product_id') is not None:reject('NEW_HAS_EXISTING_CANONICAL_IDENTITY');continue
            gates=('canonical_absence_verified','source_identity_verified','typed_identity_verified','section_verified','cross_source_dedup_verified','machine_kind_verified')
            if not all(r.get(g) is True for g in gates) or not r.get('evidence_ref'):reject('NEW_STRICT_PROOF_INCOMPLETE');continue
            p=by_payload.get(key)
            if not p:reject('NEW_FULL_PAYLOAD_MISSING');continue
            validate([r],[p])
            if r['new_group_id'] in new_groups:reject('DUPLICATE_NEW_GROUP_REQUIRES_CANONICAL_CARD_PROOF');continue
            new_groups.add(r['new_group_id']);new.append({'row':r,'payload':p});continue
        if cls!='EXISTING_CONFIRMED':reject('NON_ACCEPTED_IDENTITY_'+cls);continue
        proof=r.get('effective_identity_evidence')
        if not proof or not effective_identity_accepted(**proof):reject('EFFECTIVE_CANONICAL_IDENTITY_UNPROVEN');continue
        canonical=str(r.get('sterbrust_product_id') or '')
        if not canonical.isdecimal() or int(canonical)<=0:reject('CANONICAL_STERBRUST_ID_MISSING');continue
        if r.get('selection_authority')!='PERSISTED_CANONICAL_SQL' or r.get('selected_offer') is not True:reject('NOT_PERSISTED_SELECTED_WINNER');continue
        if not all(r.get(k) is True for k in ('supplier_enabled','offer_active','source_active','selection_eligible_verified','canonical_active')):reject('WINNER_NOT_ELIGIBLE');continue
        if r.get('price_state') not in ('numeric','numeric_public') or r.get('price_basis') not in {'exact','fixed','observed_numeric_exact'}:reject('PRICE_NOT_EXACT_NUMERIC');continue
        price=numeric(r.get('price'))
        if price is None or r.get('currency') not in {'RUB','EUR','USD'} or r.get('price_type') not in SUPPORTED_PRICE_TYPES:reject('PRICE_CURRENCY_OR_ROLE_INVALID');continue
        old=current_prices.get(canonical)
        if not old or old.get('authoritative_current') is not True or not old.get('source_ref') or not old.get('observed_at'):reject('CURRENT_STERBRUST_PRICE_UNPROVEN');continue
        old_price=numeric(old.get('price'))
        if old_price is None or old.get('currency')!=r['currency'] or old.get('price_type')!=r['price_type']:reject('CURRENT_PRICE_NOT_COMPARABLE');continue
        if old_price==price:reject('PRICE_UNCHANGED');continue
        if canonical in price_ids:raise ValueError('Duplicate canonical price operation')
        price_ids.add(canonical);prices.append({'row':r,'old_price':format(old_price,'f'),'new_price':format(price,'f'),'comparison':old})
    new_source={identity(x['row']) for x in new};price_source={identity(x['row']) for x in prices}
    if new_source & price_source:raise ValueError('NEW/PRICE source identity overlap')
    return {'scope':list(scope),'new':new,'prices':prices,'excluded':excluded,'disjoint':True}


def operation_xml(plan,operation,input_hashes):
    if operation not in {'NEW_PRODUCTS','PRICE_UPDATES'}:raise ValueError('Unknown operation')
    items=plan['new'] if operation=='NEW_PRODUCTS' else plan['prices']
    root=ET.Element('UniversalSupplier',{'contract':CONTRACT,'profile':PROFILE,'operation':operation,
        'release_scope':','.join(plan['scope']),'status':'READY_ARTIFACT','import_authorized':'false','count':str(len(items))})
    _json(root,'FrozenInputs',input_hashes)
    bucket=ET.SubElement(root,'NEW' if operation=='NEW_PRODUCTS' else 'PRICE_UPDATES')
    for item in items:
        r=item['row'];attrs={'source_ref':trace_id(r),'namespace':r['source'],'external_id':str(r['external_id'])}
        if operation=='NEW_PRODUCTS':
            attrs.update(new_group_id=r['new_group_id'],new_candidate_id=r['new_candidate_id'])
            node=ET.SubElement(bucket,'Product',attrs)
            _json(node,'SourceIdentity',{k:r.get(k) for k in ('name','brand','model','execution','sku','source_url')})
            card=ET.SubElement(node,'FullCard',{'readiness':'READY_TO_CREATE_FULL'})
            for k,v in sorted(item['payload']['fields'].items()):_json(card,k,v)
            _json(card,'description_html',item['payload'].get('description_html'))
            _json(card,'optional_configuration',item['payload'].get('optional_characteristics',[]))
            _json(node,'ReadinessProof',{k:v for k,v in r.items() if 'verified' in k or 'evidence' in k or k=='readiness_authority'})
        else:
            attrs['sterbrust_product_id']=str(r['sterbrust_product_id']);node=ET.SubElement(bucket,'Product',attrs)
            _json(node,'CommercialObservation',{'numeric_price':item['new_price'],'currency':r['currency'],'price_type':r['price_type']})
            _json(node,'PriceComparisonEvidence',{'old_price':item['old_price'],'current_state':{k:item['comparison'].get(k) for k in ('price','currency','price_type','authoritative_current','source_ref','observed_at')},'selected_offer_id':r['offer_id'],
                'source_product_id':r['source_product_id'],'selection_authority':'PERSISTED_CANONICAL_SQL'})
    ET.indent(root,space='  ')
    body=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    read=ET.fromstring(body)
    if int(read.attrib['count'])!=len(read.findall('.//Product')):raise ValueError('Operation readback count differs')
    if operation=='NEW_PRODUCTS' and any('sterbrust_product_id' in n.attrib for n in read.findall('.//Product')):raise ValueError('NEW leaked canonical ID')
    if operation=='PRICE_UPDATES' and any(n.find('FullCard') is not None for n in read.findall('.//Product')):raise ValueError('Price operation leaked product content')
    return body


def operation_manifest(plan,input_hashes):
    result={'contract':CONTRACT,'profile':PROFILE,'scope':plan['scope'],'status':'READY_ARTIFACT / NOT_IMPORT_AUTHORIZED',
            'new_intersection_price_updates':0,'REVIEW_leakage':0,'CONFLICT_leakage':0,'false_match_leakage':0,'duplicate_operations':0,'files':{}}
    for op in ('NEW_PRODUCTS','PRICE_UPDATES'):
        body=operation_xml(plan,op,input_hashes)
        if body!=operation_xml(plan,op,input_hashes):raise ValueError('Nondeterministic operation XML')
        result['files'][op+'.xml']={'count':len(plan['new'] if op=='NEW_PRODUCTS' else plan['prices']),
                                  'size':len(body),'sha256':hashlib.sha256(body).hexdigest()}
    return result
