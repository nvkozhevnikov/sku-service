"""Authenticated one-click approval; native queue, parsers and worker unchanged."""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess

from .supplier_automation import digest, settings_for, validate_settings, rate_for
from .host_fetch import MANUAL_POLICIES, host_key, policy_digest
from .rate_control import DomainRatePolicy

KIND = 'authenticated-manual-collection/v1'
ROOT = Path(__file__).resolve().parents[2]


def enabled():return os.environ.get('STERBRUST_MANUAL_COLLECTION_ENABLED') == 'YES'


def require(condition, message):
    if not condition:raise PermissionError(message)


def signature(value, key):
    require(isinstance(key, str) and len(key) >= 32, 'Ключ авторизации панели не настроен')
    return hmac.new(key.encode(), (KIND + ':' + digest(value)).encode(), hashlib.sha256).hexdigest()


@dataclass
class ManualRequest:
    actor: object
    key: str = field(repr=False)
    minimum: str
    maximum: str
    revision: int
    mode: str = 'REFRESH'

    def validate(self, supplier_id, limit, trigger, mode):
        from .source_canary import limit_value
        from .access_policy import allowed
        require(enabled(), 'Ручной запуск на этой установке выключен')
        require(self.actor.id > 0 and self.actor.is_active and not self.actor.must_change_password
                and self.actor.role in ('ADMIN','OPERATOR') and allowed(self.actor,'supplier.manage'), 'Нет прав на запуск сбора')
        require(type(supplier_id) is int and supplier_id>0 and trigger=='manual'
                and mode==self.mode and mode in ('REFRESH','DISCOVERY'), 'Недопустимый режим ручного сбора')
        limit_value(limit)

    def preview(self, queue, connection, cursor, sid, supplier, limit):
        code, adapter, base, is_enabled, crawl, old_rate = supplier
        from .source_jobs import RUNTIME_SOURCES
        from universal_supplier.adapters.ivstankomaster import CODE as IVSTANKOMASTER
        require(code in RUNTIME_SOURCES and code == adapter, 'Сбор для этого источника не поддерживается')
        require(self.mode=='REFRESH' or code==IVSTANKOMASTER, 'Поиск по всему каталогу доступен только для Ивстанкомастер')
        cfg=settings_for({'rate_settings':old_rate,'crawl_settings':crawl})
        require(cfg['revision']==self.revision, 'Настройки изменились. Обновите страницу')
        changed=validate_settings({'delay_min_seconds':self.minimum,'delay_max_seconds':self.maximum},cfg)
        if self.actor.role!='ADMIN':
            require(all(changed[n]==cfg[n] for n in ('delay_min_seconds','delay_max_seconds')), 'Изменить скорость может только администратор')
            saved=(crawl or {}).get('host_policy_approval') or {}
            require(saved.get('approved_rate_sha256')==digest(rate_for(cfg,old_rate)), 'Скорость должен сначала подтвердить администратор')
        rate=rate_for(changed,old_rate)
        if code==IVSTANKOMASTER:
            require(rate['min_request_interval_seconds']>=2 and rate['max_concurrency']==1,
                    'Для Ивстанкомастер нужна пауза от двух секунд и один поток')
        self.nonce=secrets.token_hex(16)
        self.supplier_id=sid
        modified=deepcopy(crawl or {})
        modified['host_policy_approval']={'host':host_key(base),'reference':'MANUAL:'+self.nonce,
            'approved_rate_sha256':digest(rate)}
        self.policy={host_key(base):{code:policy_digest(code,host_key(base),DomainRatePolicy.from_mapping(rate),modified['host_policy_approval']['reference'])}}
        token=MANUAL_POLICIES.set(self.policy)
        try:
            return queue._canary_preview(connection,cursor,sid,(code,adapter,base,is_enabled,modified,rate),limit,self.mode)
        finally:MANUAL_POLICIES.reset(token)

    def seal(self, snapshot, cursor):
        from .source_canary import TARGET
        from .source_runtime import engine_fingerprint
        cursor.execute('SELECT id,catalog_product_id FROM source_products WHERE catalog_product_id IS NOT NULL ORDER BY id')
        # DB-API cursor.execute is not required to return the cursor.
        mappings=[list(row) for row in cursor.fetchall()]
        value={'format':KIND,'nonce':self.nonce,'target':TARGET,
            'actor_id':self.actor.id,'actor_role':self.actor.role,'actor_sv':self.actor.session_version,
            'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'engine':engine_fingerprint(),'snapshot_sha256':digest(snapshot),'policies':self.policy,
            'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=snapshot['crawl_settings']['max_run_duration_seconds']+300)).isoformat(),
            'matching_apply':False,'mappings':mappings}
        return {**value,'signature':signature(value,self.key)}


def verify(snapshot, key, *, now=None):
    from .source_canary import TARGET, validate_input
    from .source_runtime import engine_fingerprint
    from .source_jobs import RUNTIME_SOURCES
    from universal_supplier.adapters.ivstankomaster import CODE as IVSTANKOMASTER
    marker=deepcopy(snapshot.get('manual_collection') or {})
    signed=marker.pop('signature', '')
    require(marker.get('format')==KIND and hmac.compare_digest(signed,signature(marker,key)), 'Подтверждение запуска повреждено')
    require(marker.get('target')==TARGET and marker.get('matching_apply') is False,'Неверная база или режим сопоставления')
    plain={k:v for k,v in snapshot.items() if k!='manual_collection'}
    require(digest(plain)==marker.get('snapshot_sha256'),'Параметры запуска изменились')
    require(marker.get('engine')==engine_fingerprint() and marker.get('head')==subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'Код изменился. Создайте новый запуск')
    clock=now or datetime.now(timezone.utc)
    require(clock < datetime.fromisoformat(marker['expires_at']), 'Время запуска истекло')
    code=snapshot['supplier_code'];canary=snapshot['source_input']['canary']
    mode=snapshot['source_input']['mode']
    require(code in RUNTIME_SOURCES and (mode=='REFRESH' or (code==IVSTANKOMASTER and mode=='DISCOVERY'))
            and snapshot['source_input']['supplier_code']==code and snapshot['adapter_name']==code
            and type(canary['max_products']) is int and 1<=canary['max_products']<=100000,'Область запуска изменилась')
    host=snapshot['domain'];rate=DomainRatePolicy.from_mapping(snapshot['rate_settings'])
    expected={host:{code:policy_digest(code,host,rate,snapshot['crawl_settings']['host_policy_approval']['reference'])}}
    require(marker['policies']==expected,'Профиль скорости изменился')
    token=MANUAL_POLICIES.set(expected)
    try:validate_input(snapshot['source_input'],snapshot['crawl_settings'])
    finally:MANUAL_POLICIES.reset(token)
    return marker


def launch(job_id, *, key=None):
    """No shell, secret arguments, background daemon or unrelated queue claims."""
    import sys
    require(type(job_id) is int and job_id>0,'Неверный номер задания')
    env=dict(os.environ)
    if key is not None:env['SESSION_SIGNING_SECRET']=key
    subprocess.Popen([sys.executable,'-B','-m','universal_supplier.control_plane.manual_worker','--job-id',str(job_id)],
        cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True,env=env)
