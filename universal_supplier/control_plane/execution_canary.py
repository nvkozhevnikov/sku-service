"""Strict release-only purpose on the existing refresh session/queue/worker."""
from .automation_execution import require
from .supplier_automation import digest
from .source_jobs import validate_url

PURPOSE='release-commercial-canary/v1'
LIVE={'partner_st','optimum'}

def require_unused(prior_jobs):require(not prior_jobs,'CANARY_APPROVAL_ALREADY_CONSUMED')

def require_unused_session(root,sha,folder):
    import json
    base=(root/'reports/automation-runtime').resolve()
    require(folder.resolve().is_relative_to(base),'CANARY_NATIVE_SESSION_DIRECTORY_REQUIRED')
    for receipt in base.glob('*/SESSION.json'):
        require(json.loads(receipt.read_text(encoding='utf-8')).get('sha256')!=sha,'CANARY_APPROVAL_ALREADY_CONSUMED')

def is_canary(value):return value.get('purpose')==PURPOSE

def validate(value):
    require(value.get('matching_apply') is False and value.get('continuation') is None and value.get('explicit_safe_resume') is False,'CANARY_NO_APPLY_OR_RESUME')
    require(value.get('commercial_only') is True and value.get('discovery') is False,'CANARY_REFRESH_ONLY')
    all_targets=[]
    for code,r in value['suppliers'].items():
        entries=r['targets'];all_targets+=entries
        require(len(entries)==(20 if code in LIVE else 0),'CANARY_EXACT_20_EACH_TWO_LIVE')
        require(r['limits']=={'REFRESH':len(entries),'DISCOVERY':0} and r['max_queue_attempts']==1,'CANARY_LIMIT_OR_RETRY')
        require(not r.get('mixed_recovery') and not r.get('robots_policy_exception'),'CANARY_NO_RECOVERY_OR_ROBOTS_EXCEPTION')
        if entries:
            require(r['settings_before']['enabled'] is True and r['settings_after']['enabled'] is True,'CANARY_NO_ACTIVATION')
            require(r['policy']['max_attempts']==1,'CANARY_SINGLE_TRANSPORT_ATTEMPT')
        else:require(r['settings_after']==r['settings_before'],'CANARY_SAVED_ONLY_SETTINGS')
        require(entries==sorted(entries,key=lambda e:e['source_product_id']),'CANARY_TARGET_ORDER')
        for e in entries:
            require(e['supplier_code']==code and e['supplier_id']==r['supplier_id'] and type(e['source_product_id']) is int and bool(e['external_id']),'CANARY_SOURCE_IDENTITY')
            validate_url(code,e['source_url'])
        require(len({e['source_url'] for e in entries})==len(entries) and len({e['external_id'] for e in entries})==len(entries),'CANARY_DUPLICATE_REQUEST')
    require(len({e['source_product_id'] for e in all_targets})==40 and value['scope_sha256']==digest(all_targets),'CANARY_SCOPE_PIN')
    require(any(e['confirmed_sterbrust_id'] for e in value['suppliers']['partner_st']['targets']),'CANARY_CONFIRMED_SAMPLE_REQUIRED')

def authorize_native(card,capture):
    from .automation_execution import require_active
    session=require_active();require(is_canary(session.value),'CANARY_ACTIVE_PURPOSE')
    rows=session.value['suppliers'][card.supplier_code]['targets']
    matches=[r for r in rows if r['external_id']==card.external_id and r['source_url']==capture.requested_url]
    require(len(matches)==1 and card.canonical_url==capture.final_url==capture.requested_url,'CANARY_NATIVE_CARD_OUTSIDE_SCOPE')
    return matches[0]
