"""Read-only presentation of recorded facts; missing phases remain unknown."""
import json
from datetime import datetime, timezone, timedelta
import re

MOSCOW = timezone(timedelta(hours=3))
_STAMP = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}')


def moscow_datetime(value):
    """Display recorded instants without changing storage or API values."""
    if not value:
        return 'Нет данных'
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(MOSCOW).strftime('%d.%m.%Y, %H:%M')
    except (ValueError, TypeError, OverflowError):
        return 'Нет данных'


def display_timestamp(value):
    return moscow_datetime(value) if isinstance(value, datetime) or isinstance(value, str) and _STAMP.match(value) else value


def supplier_display_name(row):
    return 'Bekamak · справочник' if row.get('code') == 'beka_mak_tr' else row.get('name') or 'Источник'


def supplier_status(value):
    return {'succeeded':'Успешно','partial':'Частично','failed':'Ошибка','running':'Выполняется',
            'queued':'В очереди','cancelled':'Остановлен','stale_failed':'Прерван после сбоя',
            'PACKAGED PASS':'Сохранённые данные'}.get(str(value or ''),'Не подтверждено')


def job_message(value):
    """Human summary; exact wire diagnostics remain in details and logs."""
    upper = str(value or '').upper()
    if 'UNKNOWN' in upper or 'UNCERTAIN' in upper:
        return 'Исход запроса не подтверждён'
    if re.search(r'HTTP[_ :]*5\d\d', upper):
        return 'Поставщик вернул ошибку'
    if 'TIMEOUT' in upper:
        return 'Превышено время ожидания ответа'
    if 'STOP' in upper:
        return 'Сбор остановлен'
    if 'ERROR' in upper or 'FAILED' in upper:
        return 'Сбор завершился с ошибкой'
    return ''

def phase_summary(row):
    if not hasattr(row,'get'):row=vars(row)
    meta=row.get('metadata') or row.get('last_run_metadata') or {}
    http=meta.get('http_phase') or {};requests=http.get('request_metrics') or {}
    identity=meta.get('identity_post_processing') or {}
    runtime=meta.get('source_runtime')
    if runtime:
        metrics=runtime.get('request_metrics') or {}
        return {'http_requested':metrics.get('requests_total'),'http_successful':metrics.get('http_2xx'),
            'parsed':runtime.get('parsed_this_job'),'http_speed':runtime.get('http_cards_per_minute'),
            'sql_count':runtime.get('sql_committed_this_job'),'sql_state':'Сверено с SQL' if runtime.get('sql_committed_this_job') else 'Новых commits не подтверждено',
            'matching':identity.get('total'),'matching_status':identity.get('status','не запускался'),
            'failure':meta.get('failure'),'termination':runtime.get('termination_reason'),'buckets':identity.get('by_source') or {}}
    committed='default_offer_counts' in meta
    matching_status=identity.get('status') or ('не запускался' if meta.get('failed_stage')=='sql_ingest_seconds' else 'результат не записан')
    return {'http_requested':requests.get('requests_total'),'http_successful':requests.get('http_2xx'),
            'parsed':http.get('successful_cards'),'http_speed':http.get('cards_per_minute'),
            'sql_count':row.get('fetched_count') if committed else None,
            'sql_state':'Сохранено' if committed else 'Ошибка SQL' if meta.get('failed_stage')=='sql_ingest_seconds' else 'Не подтверждено',
            'matching':identity.get('total'),'matching_status':matching_status,
            'failure':meta.get('failure'),'termination':meta.get('termination_reason'),
            'buckets':(identity.get('by_source') or {})}

def price_label(row):
    state=row.get('commercial_price_state') or row.get('price_state')
    if state=='price_on_request':return 'По запросу'
    price=row.get('commercial_price')
    if price is None:price=row.get('price')
    if price is None or state in ('missing','invalid'):return 'Цена не указана'
    return f"{price} {row.get('commercial_currency') or row.get('currency') or ''}".strip()

def duration_label(row):
    a,b=row.get('started_at'),row.get('finished_at')
    if a is None or b is None:return 'нет данных'
    try:
        if isinstance(a,str):a=datetime.fromisoformat(a)
        if isinstance(b,str):b=datetime.fromisoformat(b)
        return f'{(b-a).total_seconds():.1f} сек.'
    except (TypeError,ValueError):return 'нет данных'

def mark_quality_repeats(rows):
    result=[dict(row) for row in rows];seen=set()
    for row in sorted(result,key=lambda x:x.get('id') or 0):
        key=json.dumps([row.get(k) for k in ('supplier_id','crawl_run_id','products_seen','products_parsed','metrics','anomaly_codes','critical')],sort_keys=True,default=str)
        row['duplicate_profile']=key in seen;seen.add(key)
    return result
