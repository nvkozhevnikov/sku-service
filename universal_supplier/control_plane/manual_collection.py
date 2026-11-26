"""Authenticated one-click approval; native queue, parsers and worker unchanged."""
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

from .supplier_automation import digest, settings_for, validate_settings, rate_for
from .host_fetch import MANUAL_POLICIES, host_key, policy_digest
from .rate_control import DomainRatePolicy

KIND = 'authenticated-manual-collection/v1'
ROOT = Path(__file__).resolve().parents[2]
MANUAL_EXECUTION = ContextVar('manual_collection_execution', default=None)
TERMINAL = ('partial','failed','cancelled','stale_failed')


def enabled():return os.environ.get('STERBRUST_MANUAL_COLLECTION_ENABLED') == 'YES'


def require(condition, message):
    if not condition:raise PermissionError(message)


def release_revision():
    """Use the checkout's HEAD locally or the revision baked into a release image."""
    try:
        if (ROOT / '.git').exists():
            revision = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
        else:
            revision = (ROOT / '.release-revision').read_text(encoding='ascii').strip()
    except (OSError, UnicodeError, subprocess.CalledProcessError) as error:
        raise PermissionError('Не удалось определить версию приложения. Проверьте сборку панели.') from error
    require(re.fullmatch(r'[0-9a-f]{40}', revision), 'Неверная версия приложения. Проверьте сборку панели.')
    return revision


def signature(value, key):
    require(isinstance(key, str) and len(key) >= 32, 'Ключ авторизации панели не настроен')
    return hmac.new(key.encode(), (KIND + ':' + digest(value)).encode(), hashlib.sha256).hexdigest()


def running_manually(job):
    return MANUAL_EXECUTION.get()==job.id and bool(job.settings_snapshot.get('manual_collection'))


@dataclass
class ManualRequest:
    actor: object
    key: str = field(repr=False)
    minimum: str
    maximum: str
    revision: int
    mode: str = 'REFRESH'
    duration_hours: int | None = None
    retry_network: bool = False

    def validate(self, supplier_id, limit, trigger, mode):
        from .source_canary import limit_value
        from .access_policy import allowed
        require(enabled(), 'Ручной запуск на этой установке выключен')
        require(self.actor.id > 0 and self.actor.is_active and not self.actor.must_change_password
                and self.actor.role in ('ADMIN','OPERATOR') and allowed(self.actor,'supplier.manage'), 'Нет прав на запуск сбора')
        require(type(supplier_id) is int and supplier_id>0 and trigger=='manual'
                and mode==self.mode and mode in ('REFRESH','DISCOVERY','RESUME'), 'Недопустимый режим ручного сбора')
        require(not self.retry_network or mode=='RESUME', 'Повтор сетевых ошибок доступен из итогов завершённого сбора')
        if mode!='RESUME':
            limit_value(limit)
        elif limit is not None or self.duration_hours is not None:
            raise ValueError('Продолжение использует сохранённые лимиты')
        if self.duration_hours is not None:
            require(self.actor.role=='ADMIN', 'Изменить время может только администратор')
            if type(self.duration_hours) is not int or not 1<=self.duration_hours<=24:
                raise ValueError('Укажите целое число часов от 1 до 24')

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
        if self.duration_hours is not None:
            rate['max_run_duration_seconds']=self.duration_hours*3600
            modified['max_run_duration_seconds']=rate['max_run_duration_seconds']
        modified['host_policy_approval']={'host':host_key(base),'reference':'MANUAL:'+self.nonce,
            'approved_rate_sha256':digest(rate)}
        self.policy={host_key(base):{code:policy_digest(code,host_key(base),DomainRatePolicy.from_mapping(rate),modified['host_policy_approval']['reference'])}}
        token=MANUAL_POLICIES.set(self.policy)
        try:
            return queue._canary_preview(connection,cursor,sid,(code,adapter,base,is_enabled,modified,rate),limit,self.mode)
        finally:MANUAL_POLICIES.reset(token)

    def resume(self, config, sid, supplier, parent_id, previous):
        from .source_controls import verify_resume
        from .source_runtime import job_dir, read_input
        from .source_resume import FROZEN_BATCH_REFUSAL, manual_redirect_receipt, manual_transport_receipt, retryable_network_urls
        status, prior, metadata=previous
        require(status in TERMINAL, 'Дождитесь завершения исходного задания')
        prior_marker=authenticated_marker(prior,self.key)
        self.robots_attempts=prior_marker.get('robots_attempts',1)
        code=supplier[0]; result=(metadata or {}).get('source_runtime') or {}
        original=prior['source_input']; scope=original['canary']
        owner=result.get('owner_job_id'); sha=result.get('input_sha256')
        require(prior['supplier_code']==code and scope['supplier_id']==sid and
                type(owner) is int and 0<owner<=parent_id and sha==digest(original),
                'Сохранённое задание относится к другой партии')
        anchor=prior.get('manual_resume')
        require((anchor and owner==anchor['owner_job_id']) or (not anchor and owner==parent_id),
                'Изменился владелец сохранённой очереди')
        self.nonce=secrets.token_hex(16)
        host=host_key(supplier[2]);rate=DomainRatePolicy.from_mapping(original['rate_policy'])
        ref=scope['host_policy_approval']['reference']
        self.policy={host:{code:policy_digest(code,host,rate,ref)}}
        token=MANUAL_POLICIES.set(self.policy)
        try:
            read_input(code,owner,sha,previous_engine=original['engine_sha256'])
            folder=job_dir(code,owner)
            state=json.loads((folder/'checkpoint.json').read_text(encoding='utf-8'))
            receipt=None
            legacy_redirect=None
            # Use only the last signed job's evidence. The checkpoint can
            # belong to the original job even if the failed request was in a child.
            reason=result.get('termination_reason')
            if original['mode']=='REFRESH' and reason in ('SOURCE_CARD_HTTP_FAILED',FROZEN_BATCH_REFUSAL):
                uncertain=[r['receipt'] for r in state['terminal'].values() if r['state']=='UNCERTAIN']
                if len(uncertain)==1:
                    item=uncertain[0]
                    if item.get('reason'):
                        for path in (job_dir(code,parent_id)/'transport-errors').glob('*.json'):
                            try:
                                if reason==FROZEN_BATCH_REFUSAL:
                                    if item['reason']!='ValueError:'+FROZEN_BATCH_REFUSAL:break
                                    legacy_redirect=manual_redirect_receipt(item['origin'],item['url'],parent_id,path,legacy=True)
                                else:
                                    receipt=manual_transport_receipt(item['origin'],item['url'],parent_id,path,
                                                                     expected_error=item['reason'])
                            except (OSError,ValueError,KeyError,TypeError):continue
                            break
            retries=[]
            if self.retry_network:
                require(not reason and not state['frontier'] and not state['inflight'],
                        'Сначала завершите оставшуюся очередь')
                retries=retryable_network_urls(state)
                require(bool(retries), 'В этом запуске нет сетевых ошибок для повторного сбора')
            plan=verify_resume(code,owner,sha,original,config,manual_isolation=legacy_redirect or receipt,
                               require_pending=not self.retry_network)
            if self.retry_network:
                require(plan is not None and not plan['offline_pending'],'Сначала завершите оставшуюся очередь')
        finally:
            MANUAL_POLICIES.reset(token)
        snapshot=deepcopy(prior)
        snapshot.pop('manual_collection')
        snapshot['crawl_settings'].update(collection_mode='RESUME',resume_owner_job_id=owner,resume_input_sha256=sha)
        snapshot['manual_resume']={'parent_job_id':parent_id,'owner_job_id':owner,
                                   'previous_engine':original['engine_sha256'],
                                   'checkpoint_sha256':state['checksum'],'legacy_timeout':receipt}
        if legacy_redirect:snapshot['manual_resume']['legacy_redirect']=legacy_redirect
        if retries:snapshot['manual_resume']['retry_network_urls']=retries
        return snapshot

    def seal(self, snapshot, cursor):
        from .source_canary import TARGET
        from .source_runtime import engine_fingerprint
        revision = release_revision()
        cursor.execute('SELECT id,catalog_product_id FROM source_products WHERE catalog_product_id IS NOT NULL ORDER BY id')
        # DB-API cursor.execute is not required to return the cursor.
        mappings=[list(row) for row in cursor.fetchall()]
        robots_attempts=getattr(self,'robots_attempts',min(2,snapshot['rate_settings']['max_attempts']))
        value={'format':KIND,'nonce':self.nonce,'target':TARGET,
            'actor_id':self.actor.id,'actor_role':self.actor.role,'actor_sv':self.actor.session_version,
            'head':revision,
            'engine':engine_fingerprint(),'snapshot_sha256':digest(snapshot),'policies':self.policy,
            'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=snapshot['crawl_settings']['max_run_duration_seconds']+300)).isoformat(),
            'matching_apply':False,'mappings':mappings,'robots_attempts':robots_attempts}
        return {**value,'signature':signature(value,self.key)}


def authenticated_marker(snapshot,key):
    from .source_canary import TARGET
    marker=deepcopy(snapshot.get('manual_collection') or {})
    signed=marker.pop('signature', '')
    require(marker.get('format')==KIND and hmac.compare_digest(signed,signature(marker,key)), 'Подтверждение запуска повреждено')
    require(marker.get('target')==TARGET and marker.get('matching_apply') is False,'Неверная база или режим сопоставления')
    plain={k:v for k,v in snapshot.items() if k!='manual_collection'}
    require(digest(plain)==marker.get('snapshot_sha256'),'Параметры запуска изменились')
    return marker


def verify(snapshot, key, *, now=None):
    from .source_canary import validate_input
    from .source_runtime import engine_fingerprint
    from .source_jobs import RUNTIME_SOURCES
    from universal_supplier.adapters.ivstankomaster import CODE as IVSTANKOMASTER
    marker=authenticated_marker(snapshot,key)
    require(marker.get('engine')==engine_fingerprint() and marker.get('head')==release_revision(),'Код изменился. Создайте новый запуск')
    clock=now or datetime.now(timezone.utc)
    require(clock < datetime.fromisoformat(marker['expires_at']), 'Время запуска истекло')
    code=snapshot['supplier_code'];canary=snapshot['source_input']['canary']
    mode=snapshot['source_input']['mode']
    resume=snapshot.get('manual_resume')
    settings=snapshot['crawl_settings']
    if settings.get('collection_mode')=='RESUME':
        require(resume and resume['owner_job_id']==settings['resume_owner_job_id']
                and resume['previous_engine']==snapshot['source_input']['engine_sha256']
                and settings['resume_input_sha256']==digest(snapshot['source_input']), 'Изменилась область продолжения')
        retries=resume.get('retry_network_urls')
        if retries is not None:
            require(isinstance(retries,list) and 0<len(retries)<=canary['max_products']
                    and all(isinstance(url,str) for url in retries) and len(set(retries))==len(retries)
                    and not any(resume.get(a) for a in ('legacy_redirect','legacy_timeout')),
                    'Изменился список повторных запросов')
    else:
        require(not resume and snapshot['source_input']['engine_sha256']==marker['engine'], 'Изменился исходный запуск')
    require(code in RUNTIME_SOURCES and (mode=='REFRESH' or (code==IVSTANKOMASTER and mode=='DISCOVERY'))
            and snapshot['source_input']['supplier_code']==code and snapshot['adapter_name']==code
            and type(canary['max_products']) is int and 1<=canary['max_products']<=100000,'Область запуска изменилась')
    host=snapshot['domain'];rate=DomainRatePolicy.from_mapping(snapshot['rate_settings'])
    robots_attempts=marker.get('robots_attempts',1)
    require(type(robots_attempts) is int and 1<=robots_attempts<=min(2,rate.max_attempts),
            'Лимит проверки правил сайта изменился')
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
