"""Safe provider diagnostics. Response text, keys and request IDs never leave transport."""
import hashlib
import json
import math
import re
import time
from collections import OrderedDict
from email.utils import parsedate_to_datetime
from threading import RLock
from urllib.parse import urlsplit


# Official Z.AI business codes: https://docs.z.ai/api-reference/api-code
ZAI_CODES = {
    '1000': ('authentication', 'Сервис не принял ключ.', 'Проверьте ключ и его тип в кабинете сервиса.'),
    '1001': ('authentication', 'Сервис не получил параметры авторизации.', 'Проверьте формат API подключения.'),
    '1003': ('authentication', 'Срок действия ключа истёк.', 'Создайте новый ключ в кабинете и сохраните его локально.'),
    '1005': ('authentication', 'Сервис требует двухфакторное подтверждение.', 'Завершите проверку в кабинете сервиса.'),
    '1113': ('balance', 'Недостаточно средств или нет пакета обычного API.', 'Проверьте Standard API Key и баланс Model API. Подписка Coding Plan — отдельный тип доступа.'),
    '1211': ('model', 'Сервис не распознал ID модели.', 'Проверьте точный ID и доступность модели для вашего ключа.'),
    '1220': ('access', 'Ключу недоступен запрошенный ресурс.', 'Проверьте доступ к модели и тип ключа в кабинете.'),
    '1261': ('input_limit', 'Исходник превышает лимит модели.', 'Сократите исходник или выберите модель с подходящим контекстом.'),
    '1302': ('rate_limit', 'Превышен лимит частоты запросов.', 'Дождитесь окончания паузы. Проверьте другие приложения, использующие этот ключ.'),
    '1305': ('overloaded', 'Сервис временно перегружен.', 'Повторите вручную после паузы; сохранённый материал не изменён.'),
    '1308': ('quota', 'Исчерпана квота использования.', 'Посмотрите время восстановления квоты в кабинете сервиса.'),
    '1309': ('plan_expired', 'Срок действия Coding Plan истёк.', 'Проверьте подписку. Для собственного Studio используйте обычный Model API.'),
    '1310': ('quota', 'Исчерпан недельный или месячный лимит.', 'Посмотрите срок восстановления лимита в кабинете.'),
    '1311': ('model_access', 'Текущий тариф не даёт доступа к модели.', 'Выберите доступную вашему ключу модель или проверьте условия тарифа.'),
    '1313': ('account_restricted', 'Сервис ограничил доступ по правилам использования.', 'Откройте уведомление в кабинете и обратитесь в поддержку сервиса.'),
    '1314': ('plan_expired', 'Корпоративный пакет истёк.', 'Обратитесь к администратору аккаунта.'),
    '1315': ('key_type', 'Тип ключа ограничен корпоративными Coding-сценариями.', 'Для собственного Studio создайте Standard API Key в кабинете Model API.'),
}
for _code in ('1316', '1317'):
    ZAI_CODES[_code] = ('quota_balance', 'Квота исчерпана, средств для дополнительного использования нет.', 'Проверьте квоту, её восстановление и баланс в кабинете.')
for _code in ('1318', '1319', '1320', '1321'):
    ZAI_CODES[_code] = ('spend_limit', 'Квота исчерпана, дополнительный расход ограничен.', 'Проверьте лимит расходов и время восстановления квоты в кабинете.')


class AIAPIError(ValueError):
    def __init__(self, status, reason, summary, action, *, code='', wait=0, tasks=''):
        self.status, self.reason, self.summary, self.action = status, reason, summary, action
        self.code, self.wait, self.tasks = code, wait, tasks
        detail = (f'AI API вернул HTTP {status}' + (f' · код Z.AI {code}' if code else '') + '.') if status is not None else 'Ответ AI API не получен полностью.'
        message = '\n'.join(filter(None, [tasks, detail, summary, action,
            f'До следующей ручной попытки: {wait} с.' if wait else '',
            'Автоповтора нет. Исходник и сохранённые версии остаются доступны.']))
        super().__init__(message)

    def for_tasks(self, tasks):
        return AIAPIError(self.status, self.reason, self.summary, self.action,
                          code=self.code, wait=self.wait, tasks=tasks)

    def public(self):
        return {'http_status': self.status, 'reason': self.reason,
                'provider_code': self.code, 'retry_after': self.wait}


def transport_error(reason, timeout):
    if reason == 'timeout':
        summary = f'Истекло время сетевого ожидания ({timeout} с).'
        action = 'Проверьте время ожидания в настройках подключения. Для GLM-5.3 используйте быстрый режим low. При необходимости готовьте каналы по отдельности.'
    elif reason == 'tls':
        summary = 'Не удалось установить защищённое соединение с сервисом.'
        action = 'Проверьте сетевое соединение, VPN/прокси и сертификаты. Проверка TLS не отключалась.'
    else:
        summary = 'Соединение с сервисом прервалось или ответ был передан не полностью.'
        action = 'Проверьте сеть и состояние сервиса. Неизвестно, завершил ли провайдер обработку; повторный запрос может расходовать квоту.'
    return AIAPIError(None, reason, summary, action)


def retry_seconds(value):
    if not isinstance(value, str) or len(value) > 120:
        return 0
    try:
        if re.fullmatch(r'[0-9]{1,8}', value.strip()):
            delay = int(value.strip())
        else:
            delay = math.ceil(parsedate_to_datetime(value).timestamp() - time.time())
        return max(0, min(86400, delay))
    except (ValueError, TypeError, OverflowError):
        return 0


def provider_error(base, status, raw, retry_after=None):
    code = ''
    is_zai = urlsplit(base).hostname == 'api.z.ai'
    if is_zai and len(raw) <= 2 * 1024 * 1024:
        try:
            data = json.loads(raw)
            error = data.get('error') if isinstance(data, dict) else None
            value = error.get('code') if isinstance(error, dict) else None
            if type(value) in (str, int) and re.fullmatch(r'[0-9]{4}', str(value)):
                code = str(value)
        except (ValueError, UnicodeError, RecursionError):
            pass
    reason, summary, action = ZAI_CODES.get(code, ('', '', ''))
    if code == '1113' and urlsplit(base).path.rstrip('/') == '/api/coding/paas/v4':
        summary = 'Нет доступных средств или пакета для выбранного API.'
        action = 'Проверьте активность Coding Plan, квоту и тип ключа в кабинете сервиса.'
    if not reason:
        if status == 429:
            reason, summary, action = ('rate_or_quota', 'Сервис отклонил запрос из-за лимита или квоты; точная причина не указана.',
                'Проверьте квоту, баланс и тип ключа в кабинете.' + (' Для Z.AI Coding Plan и Model API доступ различается.' if is_zai else ''))
        elif status in (401, 403):
            reason, summary, action = ('access', 'Сервис не подтвердил авторизацию или доступ.', 'Проверьте ключ, его тип и доступ к модели.')
        elif status == 404:
            reason, summary, action = ('endpoint', 'Сервис не нашёл адрес запроса или модель.', 'Проверьте базовый URL, формат API и ID модели.')
        elif status >= 500:
            reason, summary, action = ('server', 'Сервис ИИ сообщил о внутренней ошибке.', 'Попробуйте вручную позже или проверьте состояние сервиса.')
        else:
            reason, summary, action = ('request', 'Сервис не выполнил запрос.', 'Проверьте адрес, модель, формат API и настройки подключения.')
    wait = (retry_seconds(retry_after) or 30) if status == 429 and reason in ('rate_limit', 'overloaded', 'rate_or_quota') else 0
    return AIAPIError(status, reason, summary, action, code=code, wait=wait)


class AICooldown:
    """Bounded in-memory guard; never waits, retries, persists keys or makes requests."""
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = RLock()

    @staticmethod
    def identity(base, token):
        return hashlib.sha256((base + '\0' + token).encode()).digest()

    def check(self, identity):
        with self.lock:
            value = self.entries.get(identity)
            if value:
                remaining = math.ceil(value[0] - time.monotonic())
                if remaining > 0:
                    error = value[1]
                    raise AIAPIError(error.status, error.reason, error.summary, error.action,
                                     code=error.code, wait=remaining)
                self.entries.pop(identity, None)

    def remember(self, identity, error):
        if error.wait:
            with self.lock:
                self.entries[identity] = (time.monotonic() + error.wait, error)
                self.entries.move_to_end(identity)
                while len(self.entries) > 100:
                    self.entries.popitem(last=False)
