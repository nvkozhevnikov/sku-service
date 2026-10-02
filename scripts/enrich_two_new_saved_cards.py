"""Generate exactly two full saved-card payloads. No HTTP/SQL/publication."""
import copy
import hashlib
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from universal_supplier.saved_full_enrichment import extract_payload
from universal_supplier.adapters._source_content import breadcrumb_category

PRE = ROOT/'reports/RC_LOCAL/TWO_NEW_IDENTITY_ACCEPTED_2026-10-02'
OUT = ROOT/'reports/RC_LOCAL/TWO_NEW_FULL_ENRICHMENT_2026-10-02'


def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    manifest=load(PRE/'ARTIFACT_SHA256.json')
    for name in ('CHECKPOINT.json','MATCHING_ACCEPTED.json','READINESS_PROOFS.json','FULL_ENRICHMENT_EXACT_SCOPE.json'):
        assert sha(PRE/name)==manifest[name],name
    scopes=load(PRE/'FULL_ENRICHMENT_EXACT_SCOPE.json'); assert len(scopes)==2
    matching=load(PRE/'MATCHING_ACCEPTED.json'); accepted=copy.deepcopy(matching)
    proofs={(p['source'],p['external_id']):p for p in load(PRE/'READINESS_PROOFS.json')}
    ledger_path=ROOT/'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01/DETAIL_ENRICHMENT.json'
    ledger={r['url']:r for r in load(ledger_path)['rows']}
    payloads=[]
    for scope in scopes:
        key=(scope['source'],scope['external_id'])
        row=next(r for r in accepted['rows'] if (r['source'],r['external_id'])==key)
        assert row['classification']=='READY_TO_CREATE_IDENTITY'
        assert row['new_group_id'] in ('NEWG-EE5998DC31FFB11ABF9C75DC','NEWG-892CFFBB6E0CCF2A054922B9')
        capture=Path(scope['capture']); assert sha(capture)==scope['capture_sha256']
        record=ledger[scope['supplier_url']]
        assert record['capture']['evidence_sha256']==scope['capture_sha256']
        augmented=dict(scope,new_group_id=row['new_group_id'],new_candidate_id=row['new_candidate_id'])
        source_html=capture.read_text(encoding='utf-8')
        payload=extract_payload(source_html,augmented,proofs[key],record['capture'].get('observed_at'))
        category,url=breadcrumb_category(source_html,scope['supplier_url'])
        payload['fields']['supplier_category']={'state':'OBSERVED' if category else 'NOT_FOUND',
            'value':{'name':category,'url':url},'provenance':dict(payload['provenance'],selector='own breadcrumb category')}
        payload['field_statuses']={state:[k for k,v in payload['fields'].items() if v['state']==state]
                                 for state in ('OBSERVED','NOT_FOUND','UNKNOWN','CONFLICT')}
        payloads.append(payload)
        row['full_product_payload_ref']='PRODUCT_CARD_PAYLOADS.json#'+row['new_candidate_id']
        row['full_enrichment']=payload
        if payload['readiness']['full_ready']:
            row['classification']='READY_TO_CREATE_FULL'
            row['readiness'].update(state='READY_TO_CREATE_FULL',full_ready=True,enrichment_blockers=[])
        assert row['sterbrust_product_id'] is None
    changed={p['external_id'] for p in payloads}
    assert all(a==b for a,b in zip(matching['rows'],accepted['rows']) if a['external_id'] not in changed)
    full=sum(p['readiness']['full_ready'] for p in payloads)
    summary={'Existing':506,'READY_TO_CREATE_IDENTITY':2-full,'READY_TO_CREATE_FULL':full,'READY_TOTAL':2,'Review':3640,'Conflict':259}
    accepted['summary']=summary; accepted['predecessor']=str(PRE/'MATCHING_ACCEPTED.json')
    OUT.mkdir(exist_ok=True)
    def save(n,v): (OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save('PRODUCT_CARD_PAYLOADS.json',payloads); save('MATCHING_ACCEPTED.json',accepted)
    markdown=['# Two saved full product-card payloads', '', 'PACKAGED-EVIDENCE only. Image URLs observed; binaries not fetched or live-verified.', '']
    for p in payloads:
        f=p['fields']; markdown += ['## '+f['brand']['value']+' '+f['model_execution']['value'], '',
            f['name']['value'], '', 'Section: '+str(f['category']['value']['id'])+' '+f['category']['value']['name'],
            'NEW_GROUP_ID: '+p['new_group_id'], '', '### Own description', '', f['description']['value'], '',
            '### All own table characteristics', '', '| Raw label | Raw value | Normalized property | Unit | Role |', '|---|---|---|---|---|']
        for r in f['characteristics']['value']:
            markdown.append('| '+' | '.join(str(r.get(k) or '').replace('|','\\|') for k in
                                ('raw_label','raw_value','normalized_property','unit','role'))+' |')
        if p['optional_characteristics']:
            markdown += ['', '### Optional characteristics (not base-machine dimensions)', '']
            markdown += ['- '+r['context']+': '+r['raw_value'] for r in p['optional_characteristics']]
        markdown += ['', '### Own original gallery URLs', '']
        markdown += ['- '+i['original_url']+' — '+(i['caption']['value'] or 'caption NOT_FOUND') for i in f['images']['value']]
        markdown += ['', 'Price: numeric NULL; '+f['price_state']['value'], 'Availability: '+str(f['availability']['value'])+' (own product metadata)',
            'NOT_FOUND: '+', '.join(p['field_statuses']['NOT_FOUND']), 'UNKNOWN: '+str(p['field_statuses']['UNKNOWN']),
            'CONFLICT: '+str(p['field_statuses']['CONFLICT']), 'READY_TO_CREATE_FULL: '+str(p['readiness']['full_ready']),
            'Source: '+p['provenance']['source_url'], 'Capture time: '+p['provenance']['observed_at'],
            'Capture SHA256: '+p['provenance']['capture_sha256'], '']
    (OUT/'PRODUCT_CARD_PAYLOADS.md').write_text('\n'.join(markdown)+'\n',encoding='utf-8')
    save('FIELD_STATUS_AUDIT.json',[{'model':p['fields']['model_execution']['value'],'statuses':p['field_statuses'],
        'full_ready':p['readiness']['full_ready'],'blockers':p['readiness']['blockers'],
        'table_characteristics':len(p['fields']['characteristics']['value']),
        'optional_characteristics':len(p['optional_characteristics']), 'images':len(p['fields']['images']['value'])} for p in payloads])
    save('TARGETED_EVIDENCE_PLAN.json',[{'source_url':p['provenance']['source_url'],'missing_evidence':p['readiness']['blockers'],
        'http_authorized':False} for p in payloads if not p['readiness']['full_ready']])
    save('INPUT_SHA256.json',{str(p):sha(p) for p in [PRE/'CHECKPOINT.json',PRE/'MATCHING_ACCEPTED.json',PRE/'FULL_ENRICHMENT_EXACT_SCOPE.json',
        PRE/'READINESS_PROOFS.json',ledger_path]+[Path(s['capture']) for s in scopes]})
    save('CHECKPOINT.json',{'status':'TWO_SAVED_NEW_FULL_ENRICHMENT_VERIFIED','resume_from':'reports/RC_LOCAL/TWO_NEW_FULL_ENRICHMENT_2026-10-02/CHECKPOINT.json',
        'predecessor':str(PRE/'CHECKPOINT.json'),'authoritative_matching':'MATCHING_ACCEPTED.json','payloads':'PRODUCT_CARD_PAYLOADS.json',
        'summary':summary,'HTTP':0,'SQL':0,'production':0,'ESOL':0,'final_xml_publication':False,'other18_processed':False,
        'next_action':'Two complete saved-source payloads available for independent review; no publication/write or GET authorization',
        'completed_do_not_repeat':['two pinned saved-card enrichment','identity matching and absence proofs']})
    assert sha(PRE/'MATCHING_ACCEPTED.json')==manifest['MATCHING_ACCEPTED.json']
    print(json.dumps(load(OUT/'FIELD_STATUS_AUDIT.json'),ensure_ascii=False))
    print(json.dumps(summary))


if __name__=='__main__':main()
