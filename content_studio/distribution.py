"""Local, explainable hashtag selection and canonical VK footer."""
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from .source_parser import parse_source
from .owned_sites import CANONICAL_SITE, OWNED_HOSTS

# tag, matching stems, operation. More rules can be added without changing UI/API.
TYPES = [
    ('ЛенточнопильныйСтанок', r'ленточно[\s-]*пильн|ленточн\w*\s+пил|band\s*saw', 'Резка'),
    ('ТокарныйСтанок', r'токарн\w*(?:[\s-]+винторезн\w*)?\s+стан|lathe', 'Точение'),
    ('ФрезерныйСтанок', r'фрезерн\w*\s+(?:стан|оборуд)|milling', 'Фрезерование'),
    ('СверлильныйСтанок', r'сверлильн|drill\s*press', 'Сверление'),
    ('ФорматноРаскроечныйСтанок', r'форматно[\s-]*раскроечн', 'Раскрой'),
    ('РейсмусовыйСтанок', r'рейсмус', 'Строгание'),
    ('ФуговальныйСтанок', r'фуговальн', 'Строгание'),
    ('КромкооблицовочныйСтанок', r'кромкооблицов', 'МебельноеПроизводство'),
    ('ШлифовальныйСтанок', r'шлифовальн\w*\s+стан|шлифовальн\w*\s+оборуд', 'Шлифование'),
    ('ЗаточнойСтанок', r'заточн\w*\s+стан|точильн', 'Заточка'),
    ('ЛазерныйСтанок', r'лазерн\w*\s+(?:стан|рез|грав)', 'ЛазернаяРезка'),
    ('ПлазменнаяРезка', r'плазменн\w*\s+(?:рез|стан)|плазморез', 'РезкаМеталла'),
    ('Листогиб', r'листогиб', 'ГибкаМеталла'),
    ('Гильотина', r'гильотин', 'РезкаМеталла'),
    ('Пресс', r'гидравлическ\w*\s+пресс|прессов\w*\s+оборуд', 'Прессование'),
    ('Компрессор', r'компрессор', 'СжатыйВоздух'),
    ('Аспирация', r'аспирац|стружкоотсос|пылеулавлив', 'УдалениеПыли'),
    ('СварочноеОборудование', r'сварочн|сварк', 'Сварка'),
    ('ЧПУ', r'\bчпу\b|\bcnc\b', 'Автоматизация'),
    ('ЦиркулярнаяПила', r'циркулярн|круглопильн', 'Распил'),
    ('ТорцовочнаяПила', r'торцовочн', 'Распил'),
    ('Электроинструмент', r'шурупов[её]рт|перфоратор|электролобзик', 'Инструмент'),
    ('Оснастка', r'\bоснастк|токарн\w*\s+патрон|фрез[аы]\b|\bсв[её]рл(?:о|а|ы|ам|ами|ах)?\b', 'Инструмент'),
]
BRANDS = {
    'Комтех': r'комтех|komtekh',
    'BELMASH': r'belmash|белмаш', 'JET': r'jet', 'PROMA': r'proma|прома',
    'ЗУБР': r'зубр|zubr', 'Энкор': r'энкор', 'Корвет': r'корвет', 'FDB': r'fdb',
    'METALMASTER': r'metalmaster|металмастер', 'Stalex': r'stalex|сталекс',
    'MetalTec': r'metaltec', 'Optimum': r'optimum', 'Holzmann': r'holzmann',
    'Holzkraft': r'holzkraft', 'Robland': r'robland', 'SCM': r'scm',
    'Makita': r'makita|макита', 'Bosch': r'bosch|бош', 'DeWALT': r'dewalt',
    'Metabo': r'metabo|метабо', 'Einhell': r'einhell', 'PATRIOT': r'patriot',
    'FUBAG': r'fubag|фубаг', 'ABAC': r'abac', 'Remeza': r'remeza|ремеза',
    'Denzel': r'denzel', 'Hikoki': r'hikoki', 'Festool': r'festool',
    'Ставр': r'ставр', 'Интерскол': r'интерскол', 'Кратон': r'кратон',
    'HOMAG': r'homag', 'KDT': r'kdt', 'Filato': r'filato', 'LTT': r'ltt',
}


def normalize_tags(value):
    if isinstance(value, str):
        value = re.split(r'[\s,;]+', value.strip()) if value.strip() else []
    if not isinstance(value, list):
        raise ValueError('Хештеги: список или строка через пробел.')
    if len(value)>20:
        raise ValueError('Не больше5 хештегов.')
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError('Некорректный хештег.')
        tag = unicodedata.normalize('NFKC', item).lstrip('#').strip()
        if not re.fullmatch(r'\w{2,64}', tag) or tag.isdecimal():
            raise ValueError('Хештег:2–64 буквы/цифры/подчёркивания, без пробелов.')
        if tag.casefold() not in [t.casefold() for t in result]:
            result.append(tag)
    if len(result) > 5 or len(result) == 1:
        raise ValueError('Выберите2–5 хештегов или оставьте поле пустым.')
    return result


def site_link(value):
    if not isinstance(value, str) or len(value) > 3000:
        raise ValueError('Некорректная ссылка публикации.')
    value = value.strip()
    if not value:
        return ''
    if value.startswith('/') and not value.startswith('//'):
        value = CANONICAL_SITE + value
    elif value.split('/', 1)[0].lower() in OWNED_HOSTS:
        value = 'https://' + value
    try:
        p = urlsplit(value)
        good = p.scheme == 'https' and p.hostname in OWNED_HOSTS and p.port in (None, 443) and not p.username and not p.password
    except ValueError:
        good = False
    if not good or re.search(r'[\s\\]|%0[ad]', value, re.I) or re.search(r'(?:[?&])(?:access_token|token|api_key|password|session)=', value, re.I):
        raise ValueError('Ссылка на наш сайт: используйте https://sterbrust.com/… или относительный путь /catalog/… без ключей доступа. Поле можно оставить пустым.')
    return urlunsplit(('https', p.hostname, p.path or '/', p.query, p.fragment))


def detect_tags(source, title=''):
    if not isinstance(title,str) or len(title)>250:
        raise ValueError('Название должно быть строкой до250знаков.')
    parsed = parse_source(source)
    texts = [b.text for b in parsed.blocks if b.kind != 'eyebrow']
    text = title + '\n' + '\n'.join(texts)
    # Titles and the beginning of the source carry more weight than incidental mentions.
    lead = title + '\n' + '\n'.join(texts[:3])
    candidates = []
    def add(tag, kind, match, score):
        if not any(x['tag'].casefold() == tag.casefold() for x in candidates):
            candidates.append({'tag': tag, 'kind': kind, 'evidence': match.group(0)[:100], 'score': score})
    wood = re.search(r'деревообраб|по\s+дерев|древесин|мебельн|фугов|рейсмус|кромкооблиц', lead, re.I)
    metal = re.search(r'металлообраб|по\s+металл|металл[ауе]\b|винторез|листогиб|гильотин|токарн\w*\s+резьб', lead, re.I)
    if not wood and not metal:
        metal = re.search(r'винторез|метрическ\w*\s+резьб|листогиб', text, re.I)
    # If the lead is ambiguous, do not force a sector from the rest of an article.
    if wood and not metal:
        add('Деревообработка', 'отрасль', wood, 100)
    if metal and not wood:
        add('Металлообработка', 'отрасль', metal, 100)
    for brand, pattern in BRANDS.items():
        m = re.search(r'(?<!\w)(?:' + pattern + r')(?!\w)', lead, re.I)
        if m:
            add(brand, 'бренд', m, 98 - m.start()/10000)
            model = re.search(r'(?<!\w)(?:' + pattern + r')\s+([A-Za-z][A-Za-z0-9-]{1,22}\d[A-Za-z0-9-]*)(?!\w)', lead, re.I)
            if model:
                add(brand + '_' + model.group(1).replace('-', '_'), 'модель', model, 70)
    shared_stems={'ТокарныйСтанок':'токарн','ЗаточнойСтанок':'заточн'}
    for i, (tag, pattern, operation) in enumerate(TYPES):
        if tag in shared_stems:
            pattern += '|'+shared_stems[tag]+r'\w*(?=[^.!?\n]{0,70}\bстанк)'
        m = re.search(pattern, lead, re.I)
        if m:
            score = 96 - i / 100
            add(tag, 'тип оборудования', m, score)
            add(operation, 'операция', m, 80)
    candidates.sort(key=lambda x: -x['score'])
    selected = candidates[:5]
    if len(selected) < 2:
        selected = []
    return {'tags': [x['tag'] for x in selected], 'candidates': candidates[:15], 'note': 'Найдено по исходнику; проверьте отрасль и тип оборудования.' if selected else 'Недостаточно точных совпадений для2–5 тегов. Добавьте свои; случайные теги не подставляются.'}


def options(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict):
        raise ValueError('Некорректные настройки публикации.')
    return {'publication_url': site_link(value.get('publication_url', '')), 'hashtags': normalize_tags(value.get('hashtags', []))}


def footer(value):
    value = options(value)
    return '\n\n'.join(x for x in [value['publication_url'], ' '.join('#' + t for t in value['hashtags'])] if x)


def apply_footer(body, previous=None, current=None):
    old = footer(previous)
    body = body.rstrip()
    new = footer(current)
    if new and (body == new or body.endswith('\n\n' + new)):
        return body
    if old and (body == old or body.endswith('\n\n' + old)):
        body = body[:-len(old)].rstrip()
    return body + ('\n\n' if body and new else '') + new
