"""Two local preview artifacts over existing identity, selection and XML contracts.

No import/write/matching, no ESOL format claim. Every exclusion is accounted for.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
from decimal import Decimal
import hashlib,json,os,re
from pathlib import Path
from xml.etree import ElementTree as ET
from sterbrust_matching.normalization import normalize_brand
from .matching_policy_v2 import normalize_identity_model
from .offer_selection import OfferSelectionPolicy,evaluate_offers
from .commercial_freshness import assess
from .control_plane.supplier_automation import settings_for,digest
from .xml_export import _append,_product_element,FALSE_MATCH_GUARDS

NOTICE='LOCAL PREVIEW ONLY / DIRECT IMPORT FORBIDDEN / BLOCKED_IMPORT_CONTRACT_EVIDENCE'
NAMES={'updates':'sterbrust_updates.preview.xml','new_products':'sterbrust_new_products.preview.xml'}


def identity(row):
    return (normalize_brand(row.get('brand_raw')),normalize_identity_model(row.get('own_model'),row.get('brand_raw')),
            str(row.get('product_kind') or ''))


def build(snapshot, *, as_of=None):
    now=as_of or snapshot['as_of']
    if now.tzinfo is None:raise ValueError('PREVIEW_AS_OF_REQUIRES_TIMEZONE')
    if snapshot.get('complete') is not True:raise ValueError('PREVIEW_SNAPSHOT_INCOMPLETE')
    suppliers={r['code']:r for r in snapshot['suppliers']}
    cfg={code:settings_for(s) for code,s in suppliers.items()}
    records=sorted(snapshot['records'],key=lambda r:(r['supplier_code'],str(r['external_id']),r['offer_id'] or 0))
    byoffer={r['offer_id']:r for r in records if r['offer_id'] is not None}
    bysource=defaultdict(list)
    for r in records:bysource[r['source_product_id']].append(r)
    freshness={r['offer_id']:assess(r,cfg[r['supplier_code']],now) for r in records if r['offer_id'] is not None}
    policy=OfferSelectionPolicy.load(Path(__file__).resolve().parents[1]/'config/offer_selection.json')
    rejected={'updates':[],'new_products':[]};updates=[];new=[];traces=[]
    def reject(kind,row,reason):
        rejected[kind].append({'source':row.get('supplier_code'),'external_id':row.get('external_id'),
                               'source_product_id':row.get('source_product_id'),'reason':reason})
    false={(s,e) for s,e,_ in FALSE_MATCH_GUARDS}|{('optimum','305')}
    confirmed_ids={str(r['sterbrust_product_id']) for r in snapshot['registry']}
    registry_keys={(normalize_brand(r.get('brand_raw')),normalize_identity_model(r.get('model_raw'),r.get('brand_raw')))
                   for r in snapshot['registry'] if r.get('model_raw')}
    emitted=set();selection_offer_ids={c.offer_id for cs in snapshot['offer_candidates'].values() for c in cs}
    for row in records:
        if row['offer_id'] not in selection_offer_ids:reject('updates',row,'NO_CURRENT_CONFIRMED_SELECTION_CANDIDATE')
    for catalog_id,candidates in sorted(snapshot['offer_candidates'].items()):
        eligible=[]
        for candidate in candidates:
            row=byoffer.get(candidate.offer_id)
            if row is None:raise ValueError('PREVIEW_SELECTION_ROW_MISSING')
            f=freshness[candidate.offer_id]
            reason=('REFERENCE_ONLY' if candidate.supplier_code=='beka_mak_tr' else
                'KNOWN_FALSE_MATCH' if (candidate.supplier_code,str(row['external_id'])) in false else
                'IDENTITY_NOT_CONFIRMED' if row.get('identity_confirmed') is not True or not row.get('confirmed_sterbrust_id') else
                'UNRESOLVED_SOURCE_QUARANTINE' if row.get('blocking_quarantine') else
                'MATCHING_PENDING_OR_STALE' if row.get('matching_current_for_capture') is not True else
                'CURRENT_REVIEW_OR_CONFLICT' if row.get('blocking_review') or row.get('match_conflicts') else
                'STALE_OR_UNVERIFIED_PRICE' if f['price']['state']!='fresh' else
                'STALE_OR_UNVERIFIED_AVAILABILITY' if f['availability']['state']!='fresh' else None)
            if reason:reject('updates',row,reason);continue
            # Observation, rather than a stale projection, supplies current values.
            eligible.append(replace(candidate,price=Decimal(str(row['observed_price'])),
                currency=row['observed_currency'],availability=row['observed_availability'],
                availability_raw=row.get('observed_availability_raw'),quantity=None))
        if not eligible:continue
        decision=evaluate_offers(catalog_id,eligible,policy)
        if decision.selection_status!='selected':
            for c in eligible:reject('updates',byoffer[c.offer_id],decision.evidence['selection_reason'])
            continue
        row=byoffer[decision.selected_offer_id];stb=str(row.get('confirmed_sterbrust_id') or '')
        if not stb or stb not in confirmed_ids:raise ValueError('PREVIEW_CANONICAL_ID_NOT_IN_REGISTRY')
        if stb in emitted:raise ValueError('PREVIEW_DUPLICATE_STERBRUST_ID')
        emitted.add(stb)
        updates.append({'sterbrust_product_id':stb,'price':row['observed_price'],'currency':row['observed_currency'],
                        'availability':row['observed_availability']})
        traces.append({'sterbrust_product_id':stb,'supplier':row['supplier_code'],'external_id':row['external_id'],
            'capture_id':row['capture_id'],'fingerprint':row['capture_fingerprint'],
            'selection':decision.evidence,'freshness':freshness[row['offer_id']]})
        for c in eligible:
            if c.offer_id!=decision.selected_offer_id:reject('updates',byoffer[c.offer_id],'LOWER_RANKED_ELIGIBLE_OFFER')
    candidate_groups=defaultdict(list);model_properties=defaultdict(set)
    for source_id,rows in sorted(bysource.items()):
        row=rows[0];code=row['supplier_code'];key=identity(row)
        reason=('REFERENCE_ONLY' if code=='beka_mak_tr' else
            'SUPPLIER_DISABLED' if not suppliers[code]['enabled'] else
            'UNRESOLVED_SOURCE_QUARANTINE' if row.get('blocking_quarantine') else
            'EXISTING_CONFIRMED_OR_PHYSICAL_LINK' if row.get('catalog_product_id') is not None or row.get('confirmed_sterbrust_id') else
            'NOT_SAFE_NEW_MATCHING_DECISION' if row.get('decision')!='SAFE_NEW_PRODUCT_CANDIDATE' or row.get('match_status')!='NEW_CANDIDATE' else
            'MATCHING_PENDING_OR_STALE' if row.get('matching_current_for_capture') is not True else
            'REVIEW_OR_CONFLICT' if row.get('blocking_review') or row.get('match_conflicts') or row.get('conflicts') else
            'UNPROVEN_SOURCE_IDENTITY' if not row.get('external_id_is_stable') or not all(key) else
            'REGISTRY_IDENTITY_EXISTS' if key[:2] in registry_keys else
            'MULTIPLE_OFFERS_REQUIRE_REVIEW' if len(rows)!=1 else
            'STALE_OR_UNVERIFIED_CONTENT' if freshness.get(row['offer_id'],{}).get('content',{}).get('state')!='fresh' else None)
        if reason:reject('new_products',row,reason);continue
        props=digest(row.get('identity_characteristics') or {})
        model_properties[key].add(props);candidate_groups[(key,props)].append(row)
    for (key,props),rows in sorted(candidate_groups.items()):
        if len(model_properties[key])>1:
            for row in rows:reject('new_products',row,'CROSS_SOURCE_PROPERTIES_REQUIRE_REVIEW')
            continue
        rows.sort(key=lambda r:(cfg[r['supplier_code']]['priority'],r['supplier_code'],str(r['external_id'])))
        row=deepcopy(rows[0]);f=freshness[row['offer_id']]
        row.update(price=row['observed_price'] if f['price']['state']=='fresh' else None,
                   currency=row['observed_currency'] if f['price']['state']=='fresh' else None,
                   availability_normalized=row['observed_availability'] if f['availability']['state']=='fresh' else None,
                   availability_raw=row.get('observed_availability_raw') if f['availability']['state']=='fresh' else None,
                   old_price=None,quantity=None,price_raw=None,price_source=row.get('price_source') if f['price']['state']=='fresh' else None)
        candidate_id='NEWC-'+digest([key,props])[:24].upper()
        new.append({'id':candidate_id,'row':row,'aliases':[{'supplier':r['supplier_code'],'external_id':r['external_id']} for r in rows]})
    roots={kind:ET.Element('sterbrust_preview',{'kind':kind,'import_allowed':'false',
        'generated_at':now.isoformat(),'contract':'BLOCKED_IMPORT_CONTRACT_EVIDENCE'}) for kind in NAMES}
    for root in roots.values():root.append(ET.Comment(NOTICE))
    for row in sorted(updates,key=lambda r:r['sterbrust_product_id']):
        node=ET.SubElement(roots['updates'],'product',{'sterbrust_product_id':row['sterbrust_product_id']})
        for key in ('price','currency','availability'):_append(node,key,row[key])
    for item in new:
        row=item['row'];details=snapshot.get('details',{}).get(row['source_product_id'],{})
        node=_product_element(row,details);node.set('candidate_id',item['id'])
        _append(node,'supplier_aliases',item['aliases']);roots['new_products'].append(node)
    data={kind:ET.tostring(root,encoding='utf-8',xml_declaration=True) for kind,root in roots.items()}
    for kind,raw in data.items():
        parsed=ET.fromstring(raw)
        if parsed.attrib.get('import_allowed')!='false':raise ValueError('PREVIEW_IMPORT_GATE_LOST')
        if kind=='updates' and any(c.tag not in ('price','currency','availability') for n in parsed for c in n):
            raise ValueError('PREVIEW_NONCOMMERCIAL_UPDATE_FIELD')
    manifest={'generated_at':now.isoformat(),'contract':NOTICE,'import_allowed':False,
        'counts':{'updates':len(updates),'new_products':len(new)},'considered_sources':len(suppliers),
        'considered_products':len(bysource),'considered_offers':len(records),
        'excluded':rejected,'exclusion_counts':{k:dict(Counter(r['reason'] for r in v)) for k,v in rejected.items()},
        'freshness':freshness,'selection_trace':traces,'coverage':snapshot.get('coverage',{}),
        'checksums':{k:hashlib.sha256(v).hexdigest() for k,v in data.items()}}
    manifest['version']=digest(manifest)
    return data,manifest


def publish(folder, data, manifest):
    """Validated immutable version directory + atomic latest pointer; no DB writes."""
    from .control_plane.source_hourly import atomic_text
    root=Path(folder);version=manifest['version']
    if not re.fullmatch('[a-f0-9]{64}',version):raise ValueError('PREVIEW_VERSION_INVALID')
    if digest({k:v for k,v in manifest.items() if k!='version'})!=version:raise ValueError('PREVIEW_MANIFEST_VERSION_CHANGED')
    # Keep Windows paths bounded; the full immutable ID remains in the manifest.
    # A prefix collision is rejected, never overwritten or silently aliased.
    directory=version[:16];target=root/'versions'/directory;target.mkdir(parents=True,exist_ok=True)
    if (target/'manifest.json').exists() and json.loads((target/'manifest.json').read_text(encoding='utf-8'))['version']!=version:
        raise ValueError('PREVIEW_VERSION_DIRECTORY_COLLISION')
    for kind,name in NAMES.items():
        raw=data[kind]
        ET.fromstring(raw)
        if hashlib.sha256(raw).hexdigest()!=manifest['checksums'][kind]:raise ValueError('PREVIEW_CHECKSUM_MISMATCH')
        path=target/name
        if path.exists() and path.read_bytes()!=raw:raise ValueError('PREVIEW_IMMUTABLE_VERSION_CHANGED')
        if not path.exists():atomic_text(path,raw.decode('utf-8'))
    atomic_text(target/'manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2,default=str))
    atomic_text(root/'latest.json',json.dumps({'version':version,'directory':directory,'manifest_sha256':hashlib.sha256((target/'manifest.json').read_bytes()).hexdigest()}))
    return target


def latest(folder, kind=None):
    root=Path(folder);pointer=json.loads((root/'latest.json').read_text(encoding='utf-8'))
    version=pointer['version']
    if not re.fullmatch('[a-f0-9]{64}',version):raise ValueError('PREVIEW_VERSION_INVALID')
    directory=pointer.get('directory',version)
    if directory not in (version,version[:16]):raise ValueError('PREVIEW_DIRECTORY_INVALID')
    target=root/'versions'/directory;raw=(target/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=pointer['manifest_sha256']:raise ValueError('PREVIEW_MANIFEST_CHANGED')
    manifest=json.loads(raw)
    if manifest['version']!=version:raise ValueError('PREVIEW_VERSION_CHANGED')
    if kind is None:return manifest
    if kind not in NAMES:raise ValueError('PREVIEW_KIND_INVALID')
    content=(target/NAMES[kind]).read_bytes()
    if hashlib.sha256(content).hexdigest()!=manifest['checksums'][kind]:raise ValueError('PREVIEW_CHECKSUM_MISMATCH')
    return content
