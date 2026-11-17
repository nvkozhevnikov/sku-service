"""Versioned per-supplier configuration over existing JSONB and native rate policy."""
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import hashlib, json, math
from zoneinfo import ZoneInfo
from .rate_control import DomainRatePolicy

VERSION = 'supplier-automation/v1'
PHASES = ('REFRESH', 'DISCOVERY')
BOOLEAN_FIELDS = ('refresh_enabled','discovery_enabled','schedule_enabled','export_updates','export_new_products')
INTEGER_FIELDS = {'refresh_interval': (60,31536000), 'discovery_interval': (60,31536000),
                  'max_concurrency': (1,8), 'retry_limit': (1,3), 'priority': (0,10000),
                  'price_ttl': (1,31536000), 'availability_ttl': (1,31536000),
                  'content_ttl': (1,31536000)}
NUMBER_FIELDS = {'delay_min_seconds': (0.001,86400), 'delay_max_seconds': (0.001,86400),
                 'request_timeout': (0.01,300), 'backoff': (0,600), 'backoff_max': (0,3600)}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',',':'), default=str).encode()).hexdigest()


def settings_for(supplier):
    rate=DomainRatePolicy.from_mapping(supplier.get('rate_settings'))
    defaults={'version':VERSION,'revision':0,'refresh_enabled':False,'discovery_enabled':False,
        'schedule_enabled':False,'export_updates':False,'export_new_products':False,
        'refresh_interval':21600,'discovery_interval':86400,'start_time':'02:00',
        'timezone':supplier.get('timezone') or 'UTC','priority':100,
        'delay_min_seconds':rate.min_request_interval_seconds+rate.jitter_min_seconds,
        'delay_max_seconds':rate.min_request_interval_seconds+rate.jitter_max_seconds,
        'max_concurrency':rate.max_concurrency,'request_timeout':rate.request_timeout_seconds,
        'retry_limit':rate.max_attempts,'backoff':rate.backoff_base_seconds,
        'backoff_max':rate.backoff_max_seconds,'price_ttl':21600,'availability_ttl':10800,
        'content_ttl':259200}
    saved=(supplier.get('crawl_settings') or {}).get('automation') or {}
    if saved and saved.get('version')!=VERSION: raise ValueError('AUTOMATION_SETTINGS_VERSION_UNKNOWN')
    return {**defaults,**saved}


def validate_settings(values, current):
    result=deepcopy(current)
    allowed=set(BOOLEAN_FIELDS)|set(INTEGER_FIELDS)|set(NUMBER_FIELDS)|{'start_time','timezone'}
    unknown=set(values)-allowed
    if unknown:raise ValueError('Неизвестные или защищённые настройки: '+', '.join(sorted(unknown)))
    for key,value in values.items():
        if key in BOOLEAN_FIELDS:
            if type(value) is not bool and (type(value) is not str or value not in ('true','false','on','off')):raise ValueError('Некорректный флаг: '+key)
            result[key]=value in (True,'true','on')
        elif key in INTEGER_FIELDS or key in NUMBER_FIELDS:
            if isinstance(value,bool):raise ValueError('Некорректное число: '+key)
            try:
                number=float(value)
            except (ValueError,TypeError):raise ValueError('Некорректное число: '+key)
            lo,hi=(INTEGER_FIELDS if key in INTEGER_FIELDS else NUMBER_FIELDS)[key]
            if not math.isfinite(number) or not lo<=number<=hi or (key in INTEGER_FIELDS and not number.is_integer()):
                raise ValueError('Значение вне допустимого диапазона: '+key)
            result[key]=int(number) if key in INTEGER_FIELDS else number
        else:result[key]=str(value)
    if result['delay_min_seconds']>result['delay_max_seconds']:raise ValueError('Минимальная задержка больше максимальной')
    if result['backoff']>result['backoff_max']:raise ValueError('backoff больше максимального')
    try:
        ZoneInfo(result['timezone'])
        parsed=time.fromisoformat(result['start_time'])
        if parsed.tzinfo is not None or parsed.second or parsed.microsecond or len(result['start_time'])!=5:raise ValueError()
    except (ValueError,KeyError):raise ValueError('Некорректное время или часовой пояс')
    rate_for(result)  # Same native contract, no additional transport.
    return result


def rate_for(settings, existing=None):
    values=DomainRatePolicy.from_mapping(existing).snapshot()
    values.update(max_concurrency=settings['max_concurrency'],
        min_request_interval_seconds=settings['delay_min_seconds'],jitter_min_seconds=0.0,
        jitter_max_seconds=settings['delay_max_seconds']-settings['delay_min_seconds'],
        request_timeout_seconds=settings['request_timeout'],max_attempts=settings['retry_limit'],
        backoff_base_seconds=settings['backoff'],backoff_max_seconds=settings['backoff_max'])
    # respect_retry_after, error thresholds and cooldown are protected existing policy.
    return DomainRatePolicy.from_mapping(values).snapshot()


def due_occurrence(settings, phase, now):
    if phase not in PHASES:raise ValueError('AUTOMATION_UNKNOWN_PHASE')
    if now.tzinfo is None:raise ValueError('Timezone-aware clock required')
    zone=ZoneInfo(settings['timezone']);local=now.astimezone(zone)
    clock=time.fromisoformat(settings['start_time']);seconds=settings[phase.lower()+'_interval']
    if seconds%86400==0:
        # Calendar days keep the preferred local time across DST changes.
        epoch=datetime(2000,1,3,tzinfo=zone).date();days=seconds//86400
        date=local.date()-timedelta(days=(local.date()-epoch).days%days)
        candidate=datetime.combine(date,clock,tzinfo=zone)
        if candidate>local:candidate=datetime.combine(date-timedelta(days=days),clock,tzinfo=zone)
        return candidate.astimezone(timezone.utc)
    anchor=datetime.combine(local.date(),clock,tzinfo=zone).astimezone(timezone.utc)
    if anchor>now:anchor=datetime.combine(local.date()-timedelta(days=1),clock,tzinfo=zone).astimezone(timezone.utc)
    return anchor+timedelta(seconds=int((now-anchor).total_seconds()//seconds)*seconds)


def next_due(settings, phase, now, last=None):
    due=due_occurrence(settings,phase,now)
    if last is not None and last<due:return due
    if last is None and due>=now:return due
    interval=settings[phase.lower()+'_interval']
    cursor=max(now,last or now)+timedelta(seconds=1)
    if interval%86400==0:
        zone=ZoneInfo(settings['timezone']);local=max(due,last or due).astimezone(zone)
        candidate=datetime.combine(local.date()+timedelta(days=interval//86400),time.fromisoformat(settings['start_time']),tzinfo=zone)
        return candidate.astimezone(timezone.utc)
    return due+timedelta(seconds=(int((cursor-due).total_seconds()//interval)+1)*interval)


def public_settings(supplier, now=None):
    cfg=settings_for(supplier);now=now or datetime.now(timezone.utc)
    result={'settings':cfg,'revision':cfg['revision'],'live_blocked_reason':'AUTOMATION_EXECUTION_APPROVAL_REQUIRED',
            'next':{},'rate_semantics':'Равномерная случайная задержка между началами запросов одного host; robots и host caps дополнительно ограничивают скорость.'}
    for phase in PHASES:
        result['next'][phase]=next_due(cfg,phase,now) if cfg['schedule_enabled'] and cfg[phase.lower()+'_enabled'] else None
    try:
        from .host_fetch import compile_policy,policy_digest,host_key
        rate=DomainRatePolicy.from_mapping(rate_for(cfg,supplier.get('rate_settings')))
        host=host_key(supplier.get('base_url') or '')
        if not host:raise ValueError('HOST_REQUIRED')
        reference=f"ADMIN:{supplier['code']}:REV:{cfg['revision']}"
        result.update(host_reference=reference,host_policy_sha256=policy_digest(supplier['code'],host,rate,reference))
        saved=(supplier.get('crawl_settings') or {}).get('host_policy_approval') or {}
        result['host_confirmation_saved']=bool(saved.get('approved_rate_sha256')==digest(rate.snapshot())
            and saved.get('host')==host and saved.get('reference'))
        # Report the same trusted executor allowlist as the queue/worker.
        # A panel database receipt must not manufacture that trust.
        compile_policy(supplier['code'],supplier['base_url'],rate_for(cfg,supplier.get('rate_settings')),
                       approval=saved)
    except (PermissionError,ValueError) as error:result['live_blocked_reason']=str(error)
    return result
