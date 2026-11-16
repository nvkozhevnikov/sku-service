"""Read-only release canary proposal. Deliberately NOT a scheduler grant.

No enqueue/HTTP/apply entrypoint: an operator must approve the exact proposal and
a separately reviewed native execution integration before any LIVE dispatch.
Historical1270 grants and mixed/uncertain recovery contracts remain untouched.
"""
from datetime import datetime,timezone
import hashlib,json,re,subprocess
from pathlib import Path
from urllib.parse import urlsplit
from .source_jobs import RUNTIME_SOURCES,validate_url
from .source_canary import TARGET
from .source_runtime import engine_fingerprint
from .supplier_automation import digest

FORMAT='sterbrust-release-canary-proposal/v1'
ROOT=Path(__file__).resolve().parents[2]
HEX=re.compile(r'[0-9a-f]{64}')

def validate(value):
    def require(ok,reason):
        if not ok:raise ValueError(reason)
    require(value.get('format')==FORMAT and value.get('execution_authorized') is False,'CANARY_PROPOSAL_ONLY')
    require(value.get('target')==TARGET,'CANARY_TARGET_CHANGED')
    require(re.fullmatch(r'[0-9a-f]{40}',str(value.get('head',''))) is not None and HEX.fullmatch(str(value.get('engine',''))) is not None,'CANARY_CODE_PIN')
    require(HEX.fullmatch(str(value.get('mappings1282_sha256',''))) is not None,'CANARY_MAPPING_PIN')
    require(value.get('matching_apply') is False and value.get('discovery') is False and value.get('production') is False and value.get('esol') is False,'CANARY_WRITE_SCOPE')
    rules=value.get('sources',{})
    require(set(rules)==set(RUNTIME_SOURCES),'CANARY_EXACT_11')
    total=0;ids=set()
    for code,rule in rules.items():
        entries=rule.get('entries',[]);total+=len(entries)
        require(rule.get('mode') in ('SAVED_ONLY','LIVE_PROPOSED') and len(entries)<=20,'CANARY_LIMIT_OR_MODE')
        require(code not in ('intervesp','beka_mak_tr') or rule['mode']=='SAVED_ONLY','CANARY_REFERENCE_OR_MIXED_LIVE_FORBIDDEN')
        require(rule['mode']!='LIVE_PROPOSED' or rule.get('supplier_enabled') is True,'CANARY_DISABLED_SOURCE')
        require(HEX.fullmatch(str(rule.get('settings_sha256',''))) is not None,'CANARY_SETTINGS_PIN')
        require(HEX.fullmatch(str(rule.get('entries_sha256',''))) is not None and rule['entries_sha256']==digest(entries),'CANARY_ENTRIES_PIN')
        require(len({row.get('url') for row in entries})==len(entries) and len({row.get('stable_external_id') for row in entries})==len(entries),'CANARY_DUPLICATE_SOURCE_TARGET')
        for row in entries:
            require(type(row.get('source_product_id')) is int and row['source_product_id']>0 and row['source_product_id'] not in ids,'CANARY_SOURCE_ID')
            ids.add(row['source_product_id'])
            require(isinstance(row.get('stable_external_id'),str) and bool(row['stable_external_id']),'CANARY_STABLE_ID_REQUIRED')
            validate_url(code,row['url'])
    require(total<=220 and value.get('product_url_count')==total,'CANARY_TOTAL_LIMIT')
    require(value.get('identity_rule')=='stable_internal_id_only_no_slug_title_merge','CANARY_IDENTITY_RULE')
    return value

def prepare(config):
    from .runtime_config import guarded_connect
    with guarded_connect(config,autocommit=True,options='-c default_transaction_read_only=on') as c:
        if c.execute("SELECT 1 FROM crawl_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise PermissionError('CANARY_ACTIVE_JOB')
        mappings=c.execute('SELECT id,catalog_product_id FROM source_products WHERE catalog_product_id IS NOT NULL ORDER BY id').fetchall()
        if len(mappings)!=1282:raise PermissionError('CANARY_ORIGINAL_MAPPING_BASELINE_CHANGED')
        suppliers=c.execute('SELECT id,code,enabled,base_url,crawl_settings,rate_settings FROM suppliers ORDER BY code').fetchall()
        rules={}
        for sid,code,enabled,base,crawl,rate in suppliers:
            rows=c.execute('''SELECT id,external_id,source_url,catalog_product_id FROM source_products
                WHERE supplier_id=%s AND active ORDER BY (catalog_product_id IS NULL),id LIMIT 20''',(sid,)).fetchall()
            entries=[dict(source_product_id=i,stable_external_id=str(ext),url=url,catalog_product_id=pid) for i,ext,url,pid in rows]
            rules[code]=dict(supplier_id=sid,supplier_enabled=enabled,
                mode='LIVE_PROPOSED' if code in ('partner_st','optimum') and enabled else 'SAVED_ONLY',
                host=urlsplit(base).hostname,settings_sha256=digest({'enabled':enabled,'crawl_settings':crawl,'rate_settings':rate}),
                entries=entries,entries_sha256=digest(entries),
                blockers=['EXACT_OPERATOR_SHA_CONFIRMATION_AND_NATIVE_EXECUTION_REVIEW_REQUIRED'] if enabled and code in ('partner_st','optimum') else ['NO_SOURCE_ACTIVATION_OR_RETRY_AUTHORIZED'])
    value=dict(format=FORMAT,created_at=datetime.now(timezone.utc).isoformat(),
        execution_authorized=False,target=dict(TARGET),
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        engine=engine_fingerprint(),mappings1282_sha256=digest(mappings),sources=rules,
        product_url_count=sum(len(r['entries']) for r in rules.values()),
        identity_rule='stable_internal_id_only_no_slug_title_merge',matching_apply=False,
        discovery=False,production=False,esol=False,
        required_execution_guards=['clean exact HEAD and engine','confirmed file SHA256 and bounded UTC window',
            'same target/settings/SQL identities and original1282 links','existing robots/host/rate/DNS/TLS guards',
            'no retries of Intervesp COMMITTED/UNCERTAIN','native receipts/STOP and zero active jobs',
            'max20 product URLs/source, max220 overall; no discovery','preserve observed_at; never manufacture freshness'])
    return validate(value)
