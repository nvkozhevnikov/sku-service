"""Thin proposal adapter for the existing supplier-neutral policy, no DB writes."""
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from collections import defaultdict
import hashlib
import json
from .offer_selection import OfferCandidate, OfferSelectionPolicy, evaluate_offers

POLICY_PATH = Path(__file__).resolve().parents[1]/'config/offer_selection.json'


def source_key(row):
    return str(row['source']),str(row['external_id'])


def input_hash(rows):
    ordered=sorted(rows,key=source_key)
    return hashlib.sha256(json.dumps(ordered,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def candidate_from_row(row):
    if row.get('classification') != 'EXISTING_CONFIRMED':
        return None,'UNCONFIRMED_MATCH'
    canonical=str(row.get('sterbrust_product_id') or '')
    if not canonical.isdecimal() or int(canonical)<=0:
        return None,'CANONICAL_ID_MISSING'
    if row.get('price_basis') == 'from_price':
        return None,'FROM_PRICE_NOT_EXACT_EXECUTION'
    context=row.get('selection_context')
    if not context or context.get('canonical_identity_verified') is not True:
        return None,'VERIFIED_PERSISTED_ELIGIBILITY_CONTEXT_MISSING'
    if str(context.get('sterbrust_product_id'))!=canonical:
        raise ValueError('Selection context canonical identity mismatch')
    values=dict(context['candidate'])
    # Mirrors the canonical store's linked-source load boundary, rather than
    # manufacturing a logical/physical canonical link for a proposed match.
    if values.get('source_catalog_product_id') is None:
        return None,'SOURCE_HAS_NO_PERSISTED_CANONICAL_LINK'
    if values.get('supplier_code')!=row['source']:
        raise ValueError('Cross-supplier selection context mismatch')
    for key in ('source_product_id','offer_id'):
        if row.get(key) is not None and values[key]!=row[key]:
            raise ValueError('Selection context PK mismatch: '+key)
    # Explicit matching/activation state cannot be contradicted by a context.
    for key in ('supplier_enabled','offer_active'):
        if key in row and row[key]!=values[key]:
            return None,'ACTIVATION_STATE_CONTRADICTION'
    for key in ('source_last_success_at','offer_last_success_at','latest_successful_full_crawl_at'):
        if isinstance(values.get(key),str): values[key]=datetime.fromisoformat(values[key])
    for key in ('price','quantity'):
        if values.get(key) is not None: values[key]=Decimal(str(values[key]))
    if row.get('price_state') in {'price_on_request','missing','ambiguous','parse_error'}:
        if values['price'] is not None:
            raise ValueError('POR/missing must have NULL numeric price in persisted context')
    elif row.get('price') is not None and values['price']!=Decimal(str(row['price'])):
        raise ValueError('Selection context commercial price drift')
    if row.get('currency') is not None and values.get('currency')!=row['currency']:
        raise ValueError('Selection context currency drift')
    if row.get('availability') is not None and values.get('availability')!=row['availability']:
        raise ValueError('Selection context availability drift')
    if 'quantity' in row and ((Decimal(str(row['quantity'])) if row['quantity'] is not None else None)!=values.get('quantity')):
        raise ValueError('Selection context quantity drift')
    return OfferCandidate(**values),None


def proposed_selection(rows, policy=None):
    policy=policy or OfferSelectionPolicy.load(POLICY_PATH)
    keys=[source_key(row) for row in rows]
    if len(keys)!=len(set(keys)): raise ValueError('Duplicate supplier-scoped offer input')
    grouped=defaultdict(list);bindings={};excluded={}
    for row in sorted(rows,key=source_key):
        candidate,reason=candidate_from_row(row)
        if candidate is None:
            excluded[source_key(row)]=reason;continue
        grouped[candidate.catalog_product_id].append(candidate)
        binding=(candidate.catalog_product_id,candidate.offer_id)
        if binding in bindings: raise ValueError('Duplicate physical offer binding')
        bindings[binding]=source_key(row)
    selected=[];decisions=[]
    for local_id,candidates in sorted(grouped.items()):
        decision=evaluate_offers(local_id,candidates,policy)
        decisions.append(asdict(decision))
        if decision.selected_offer_id is not None:
            selected.append(list(bindings[(local_id,decision.selected_offer_id)]))
    return {'status':'VERIFIED_GENERIC_POLICY_PROPOSED_SELECTION',
            'rule_version':policy.version,'input_sha256':input_hash(rows),
            'policy_sha256':hashlib.sha256(json.dumps(policy.as_jsonable(),sort_keys=True).encode()).hexdigest(),
            'selected':sorted(selected),'decisions':decisions,
            'excluded':[{'source':k[0],'external_id':k[1],'reason':v} for k,v in sorted(excluded.items())],
            'overall_status':'SELECTED' if selected else 'NO_ELIGIBLE_OFFER',
            'SQL_writes':0,'HTTP':0}


def persisted_selection_keys(rows, state=None):
    """Exporter reads verified decisions; never invokes ranking/reselection."""
    if state is None: return set()  # No evidence means no selected label.
    if state.get('status') not in {'VERIFIED_GENERIC_POLICY_PROPOSED_SELECTION', 'LIVE_VERIFIED_PERSISTED_CANONICAL_SELECTION'} or state.get('input_sha256')!=input_hash(rows):
        raise ValueError('Persisted selection state/input mismatch')
    if state.get('status')=='LIVE_VERIFIED_PERSISTED_CANONICAL_SELECTION' and not state.get('persisted_table_sha256'):
        raise ValueError('Committed SQL selection fingerprint missing')
    by_key={source_key(row):row for row in rows};selected=set()
    for key in state['selected']:
        key=tuple(key)
        if key not in by_key:raise ValueError('Selection outside source inputs')
        candidate,reason=candidate_from_row(by_key[key])
        if (candidate is None or not candidate.supplier_enabled or not candidate.offer_active
                or candidate.source_catalog_product_id!=candidate.catalog_product_id):
            raise ValueError('Persisted winner no longer has confirmed active eligibility')
        selected.add(key)
    return selected
