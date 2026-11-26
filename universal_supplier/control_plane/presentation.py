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


def compact_number(value):
    """Format controls without exposing binary float tails; no stored changes."""
    try:
        return format(float(value), '.12g')
    except (ValueError, TypeError):
        return str(value)


def collection_notice(code):
    notices = {
        'HOST_REQUIRED': ('Не указан сайт источника',
            'Администратору нужно проверить адрес сайта в настройках источника.'),
        'HOST_PROFILE_NOT_APPROVED': ('Подтвердите настройки скорости',
            'Администратор должен подтвердить выбранную нагрузку на сайт. Это не запускает сбор.'),
        'HOST_PROFILE_DIGEST_NOT_APPROVED': ('Настройки нагрузки не разрешены исполнителю',
            'Настройки скорости не переданы исполнителю. Администратору нужно однократно согласовать этот профиль в его конфигурации.'),
        'EXPLICIT_HOST_PROFILE_REQUIRED': ('Не настроена скорость сбора',
            'Для этого источника нужно задать и подтвердить параметры нагрузки.'),
        'SOURCE_RUNTIME_NOT_ENABLED': ('Исполнитель сбора выключен',
            'Администратору нужно включить штатный исполнитель заданий.'),
        'SOURCE_WRITES_NOT_AUTHORIZED': ('Не разрешено сохранение результатов',
            'Администратору нужно разрешить штатному исполнителю сохранять результаты в выбранную базу.'),
        'SUPPLIER_HTTP_NOT_AUTHORIZED': ('Не разрешены запросы к сайту',
            'Для нового сбора требуется действующее разрешение на запросы к этому источнику.'),
        'AUTOMATION_EXECUTION_APPROVAL_REQUIRED': ('Настройки скорости проверены',
            'Укажите количество карточек. Перед запуском проверьте выбранную партию и подтвердите действующий допуск.'),
    }
    title, explanation = notices.get(code, ('Сбор пока недоступен',
        'Параметры не прошли проверку. Администратору нужно проверить настройки; техническая причина доступна ниже.'))
    return {'title': title, 'explanation': explanation}


MANUAL_RESUME_BLOCKERS = {
    'UNCERTAIN_OR_FAILED_RESUME': 'Нужна сверка карточек с неподтверждённым результатом. Очередь сохранена.',
    'UNCERTAIN_OR_FAILED_RESUME_REQUIRES_OPERATOR': 'Нужна сверка карточек с неподтверждённым результатом. Очередь сохранена.',
    'UNCERTAIN_PRODUCT_RECEIPT_REQUIRES_OPERATOR': 'Нужна сверка незавершённого запроса. Очередь сохранена.',
    'UNCERTAIN_DISCOVERY': 'Нужна сверка последней проверенной страницы каталога.',
    'RESUME_CHECKPOINT_MISSING': 'Файл сохранённой очереди недоступен.',
    'RESUME_CHECKPOINT_CHANGED': 'Не удалось подтвердить целостность сохранённой очереди.',
    'RESUME_PARENT_NOT_LATEST': 'У поставщика есть более новое задание. Откройте его в разделе «Сбор и запуски».',
    'RESUME_SUPPLIER_DISABLED': 'Источник отключён. Администратор может продолжить сбор.',
}


def error_presentation(message, path):
    """Deterministic same-site GET links, independent of history/Referer/JS."""
    code = str(message)
    supplier = re.fullmatch(r'/suppliers/([1-9][0-9]*)(?:/.*)?', path)
    if supplier:
        target = '/suppliers/' + supplier.group(1)
        if code.startswith(('HOST_', 'EXPLICIT_HOST_')):
            target += '/parsing'
        label = 'К настройкам сбора' if target.endswith('/parsing') else 'К источнику'
    else:
        target = '/review' if path.startswith('/review') else '/suppliers'
        label = 'К проверке товаров' if target == '/review' else 'К поставщикам'
    if code in {'HOST_PROFILE_NOT_APPROVED', 'HOST_PROFILE_DIGEST_NOT_APPROVED',
                'EXPLICIT_HOST_PROFILE_REQUIRED', 'SOURCE_RUNTIME_NOT_ENABLED',
                'SOURCE_WRITES_NOT_AUTHORIZED', 'SUPPLIER_HTTP_NOT_AUTHORIZED'}:
        notice = collection_notice(code)
    elif code in MANUAL_RESUME_BLOCKERS:
        notice = {'title': 'Продолжение недоступно', 'explanation': MANUAL_RESUME_BLOCKERS[code]}
    elif re.fullmatch(r'[A-Z][A-Z0-9_ :;=.-]*', code):
        notice = {'title': 'Действие заблокировано',
                  'explanation': 'Проверка безопасности не пройдена. Сбор не запущен; обратитесь к администратору.'}
    else:
        notice = {'title': code, 'explanation': 'Действие не выполнено. Проверьте указанные данные.'}
    return {**notice, 'back_url': target, 'back_label': label}


def job_message(value):
    """Human summary; exact wire diagnostics remain in details and logs."""
    upper = str(value or '').upper()
    if upper == 'ORCHESTRATOR_LEASE_EXPIRED_UNCERTAIN_RESULT':
        return 'Исполнитель перестал отвечать. Прерванное задание закрыто, сохранённые данные доступны.'
    if upper == 'MANUAL_WAITING_CANCELLED':
        return 'Ожидающее задание закрыто до начала сбора.'
    if upper == 'MANUAL_WORKER_BUSY':
        return 'Исполнитель был занят. Можно создать новый запуск.'
    if upper == 'MANUAL_WORKER_REFUSED':
        return 'Подтверждение запуска не прошло проверку. Откройте поставщика, чтобы создать новый запуск.'
    if upper in {'SOURCE_ROBOTS_UNAVAILABLE','CANARY_ROBOTS_REQUEST_LIMIT'}:
        return 'Не удалось получить правила сайта (robots.txt). До карточек сбор не дошёл.'
    if upper in {'ROBOTS_NOT_PROVEN','ROBOTS_NOT_TEXT'}:
        return 'Сайт не предоставил пригодный robots.txt. Карточки не запрашивались.'
    if upper == 'SOURCE_CARD_HTTP_FAILED':
        return 'На товарной странице произошёл сетевой сбой. Подробности сохранены в запуске.'
    if upper == 'CANARY_URL_OUTSIDE_FROZEN_BATCH':
        return 'Один адрес вывел за пределы списка этого сбора. Переход заблокирован, очередь сохранена.'
    if upper == 'MANUAL_REDIRECT_TARGET_NOT_ALLOWED':
        return 'Поставщик перенаправил на недопустимый адрес. Переход заблокирован, очередь сохранена.'
    if upper in {'SOURCE_NETWORK_CIRCUIT_OPEN','SOURCE_CARD_ERROR_THRESHOLD'}:
        return 'Несколько карточек подряд завершились ошибкой. Сбор остановлен, очередь сохранена.'
    if upper == 'SOURCE_NETWORK_RETRY_OUTSIDE_WINDOW':
        return 'До конца окна сбора не хватило времени для повторной проверки связи. Очередь сохранена.'
    if upper == 'MAX_RUN_DURATION_EXCEEDED':
        return 'Истекло время сбора. Необработанные карточки остались в очереди.'
    if upper.startswith('SOURCE_BLOCKED_HTTP_403'):
        return 'Сайт запретил доступ. Сбор остановлен.'
    if upper.startswith(('SOURCE_BLOCKED_HTTP_429', 'SOURCE_RETRY_AFTER_HTTP_503')):
        return 'Сайт попросил сделать паузу. Сбор остановлен.'
    if 'EXISTING_LINK_CONFLICT' in upper:
        return 'Данные карточки противоречат существующей связи. Прежняя связь сохранена.'
    if upper in {'HTTP_404', 'HTTP_410'}:
        return 'Карточка недоступна по сохранённому адресу.'
    if upper.startswith(('REMOTEDISCONNECTED:', 'CONNECTIONRESETERROR:', 'CONNECTIONABORTEDERROR:',
                         'BROKENPIPEERROR:', 'INCOMPLETEREAD:')):
        return 'Соединение оборвалось до полной загрузки товарной страницы.'
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


def collection_issue_counts(receipts):
    counts = {'matching_conflicts': 0, 'other_card_issues': 0}
    for receipt in receipts or ():
        category = (receipt.get('isolation') or {}).get('category')
        if category == 'matching':
            counts['matching_conflicts'] += 1
        elif category not in ('manual_read_timeout', 'manual_remote_disconnect'):
            counts['other_card_issues'] += 1
    return counts


def run_collection_progress(metadata):
    runtime = metadata.get('source_runtime') or {}
    return {**(metadata.get('progress') or {}), **collection_issue_counts(runtime.get('quarantined'))}


def collection_issue_message(receipt):
    isolation = receipt.get('isolation') or {}
    reason = isolation.get('reason')
    category = isolation.get('category')
    if category == 'manual_remote_disconnect':
        return 'Соединение оборвалось. Карточка отложена.'
    if category == 'manual_read_timeout':
        return 'Карточка не ответила вовремя и была отложена.'
    if category == 'manual_out_of_scope_redirect':
        return 'Карточка ведёт на другой адрес. Исходный адрес отложен для проверки.'
    if category == 'manual_stop_interrupted':
        return 'Сбор остановлен во время запроса. Карточка оставлена на проверку.'
    if category == 'matching':
        return job_message(reason) if 'EXISTING_LINK_CONFLICT' in str(reason) else 'Обновление требует проверки сопоставления.'
    if category == 'identity':
        return 'Не удалось подтвердить, что это тот же товар. Карточка пропущена.'
    if category == 'parse':
        return 'Не удалось разобрать данные карточки. Карточка пропущена.'
    if category == 'http':
        return job_message(reason) or 'Сайт вернул ошибку для этой карточки.'
    return 'Карточка требует проверки.'

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
