from dataclasses import dataclass, field, asdict
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, urljoin

SOURCES = {
    'kuvalda_nnov': {'name': 'Кувалда / Нижний Новгород', 'base_url': 'https://nnov.kuvalda.ru/', 'region': 'Нижний Новгород', 'seeds': ['/', '/promo/', '/news/', '/sale/']},
    'metalmaster': {'name': 'MetalMaster', 'base_url': 'https://metalmaster.ru/', 'region': 'Москва (source display)', 'seeds': ['/', '/c_actions/', '/news/', '/top_action/']},
}
TRACKING = {'yclid', 'gclid', 'etext', 'fbclid', 'ysclid', '_openstat', 'roistat', 'from', 'referrer'}

def canonical(url, base=None):
    p = urlsplit(urljoin(base or '', url.strip()))
    if p.scheme not in ('http', 'https') or p.username or p.password:
        raise ValueError('Only public HTTP(S) URLs allowed')
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in TRACKING]
    # Never discard region, product, filter or pagination parameters.
    path = p.path or '/'
    if '.' not in path.rsplit('/', 1)[-1] and not path.endswith('/'):
        path += '/'
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(sorted(q)), ''))

def text(value):
    return re.sub(r'\s+', ' ', value or '').strip()

def money(value):
    m = re.fullmatch(r'\s*(\d[\d\s\u00a0\u202f]*(?:[,.]\d{1,2})?)\s*(?:₽|руб\.?|RUB)?\s*', value or '', re.I)
    if not m:
        return None
    try:
        n = Decimal(re.sub(r'[\s\u00a0\u202f]', '', m[1]).replace(',', '.'))
        return n if n > 0 else None
    except InvalidOperation:
        return None

MONTHS = {m: i+1 for i, m in enumerate(['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'])}

def parse_date(value):
    value = text(value).lower()
    m = re.search(r'(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)', value)
    if m:
        parts = tuple(map(int, m.groups()))
    else:
        m = re.search(r'(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)', value)
        if m:
            d, mo, y = map(int, m.groups()); parts = (y, mo, d)
        else:
            m = re.search(r'(\d{1,2})\s+('+'|'.join(MONTHS)+r')\s+(\d{4})', value)
            if not m:
                return None
            parts = (int(m[3]), MONTHS[m[2]], int(m[1]))
    try:
        return date(*parts)
    except ValueError:
        return None

def campaign_dates(value, explicit_year=None):
    """Yearless ranges remain unknown unless a publication date proves the year."""
    value = text(value).lower()
    start = end = None
    mo = '|'.join(MONTHS)
    m = re.search(r'(?:с\s+)?(\d{1,2})\s+(?:по|[-–—])\s*(\d{1,2})\s+('+mo+r')(?:\s+(\d{4}))?', value)
    if m and (m[4] or explicit_year):
        year = int(m[4] or explicit_year)
        start = parse_date(f'{m[1]} {m[3]} {year}')
        end = parse_date(f'{m[2]} {m[3]} {year}')
    m = re.search(r'с\s+(\d{1,2}\s+(?:'+mo+r')(?:\s+\d{4})?)\s+(?:по|до)\s+(\d{1,2}\s+(?:'+mo+r')(?:\s+\d{4})?)', value)
    if m:
        start = parse_date(m[1]) or (parse_date(m[1]+' '+str(explicit_year)) if explicit_year else None)
        end = parse_date(m[2]) or (parse_date(m[2]+' '+str(explicit_year)) if explicit_year else None)
    m = re.search(r'(?:действует|действуют|продлится|акция[^.!]{0,20})\s+до\s+(\d{1,2}\s+(?:'+mo+r')\s+\d{4})', value)
    if m:
        end = parse_date(m[1])
    return start, end

def inclusion(published, valid_from, valid_to, current_promo, since, today):
    if published and since <= published <= today:
        return 'PUBLICATION'
    if valid_to and valid_to >= since and (valid_from is None or valid_from <= today):
        return 'CAMPAIGN_OVERLAP' if valid_from else 'END_DATE_OVERLAP_START_UNKNOWN'
    if valid_from and since <= valid_from <= today and valid_to is None:
        return 'START_DATE_OPEN_END'
    if current_promo and published is None and (valid_to is None or valid_to >= since):
        return 'CURRENT_PROMO'
    return None

def priority(name, category=''):
    s = (name+' '+(category or '')).lower()
    if re.search(r'оснастк|патрон|полотн|сверл[ао]\b|расходн|аксессуар|запчаст|для станк|ручн.*пил', s):
        return 'LOW'
    if re.search(r'станок|станки|токарн|фрезерн|сверлильн|ленточнопильн|листогиб|гильотин|пресс|вальц|шлифовальн|промышленн|сварочн.*(аппарат|оборуд)|компрессорн.*станц', s):
        return 'HIGH'
    if re.search(r'инструмент|болгарк|шурупов|теплов|снегоубор|газон|мотокос|подарок', s):
        return 'LOW'
    return 'NORMAL'

def price_type(label, old=None):
    s = (label or '').lower()
    for pattern, kind in [(r'после авторизац|авториз|войдите', 'AUTH_REQUIRED'), (r'персональн', 'PERSONAL'), (r'/\s*мес|в месяц|₽/мес|ежемесяч', 'INSTALLMENT_MONTHLY'), (r'плат[её]ж.*кредит', 'CREDIT_PAYMENT'), (r'бонус|балл', 'BONUS'), (r'до\s*[-−]?\s*\d+\s*%', 'DISCOUNT_PERCENT_ONLY'), (r'\bот\s*\d', 'FROM_PRICE')]:
        if re.search(pattern, s):
            return kind
    return 'SALE' if old is not None else 'PUBLIC'

@dataclass
class Item:
    product_name: str
    product_url: str | None = None
    campaign_name: str | None = None
    tab_title: str | None = None
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    old_price: Decimal | None = None
    new_price: Decimal | None = None
    currency: str = 'RUB'
    discount_amount: Decimal | None = None
    discount_percent: Decimal | None = None
    displayed_discount_raw: str | None = None
    price_type: str = 'UNKNOWN'
    price_label_raw: str | None = None
    availability: str | None = None
    region: str | None = None
    priority: str = 'NORMAL'
    evidence_html: str = ''
    warnings: list = field(default_factory=list)

    def finish(self):
        self.priority = priority(self.product_name, self.category)
        if self.price_type in ('INSTALLMENT_MONTHLY', 'CREDIT_PAYMENT', 'BONUS', 'DISCOUNT_PERCENT_ONLY'):
            self.new_price = self.old_price = None
        if self.old_price and self.new_price:
            if self.new_price >= self.old_price:
                self.warnings.append('INVALID_OLD_NEW_PRICE')
                self.old_price = None
            else:
                self.discount_amount = self.old_price - self.new_price
                self.discount_percent = (100*self.discount_amount/self.old_price).quantize(Decimal('.01'))
                m = re.search(r'(\d+(?:[.,]\d+)?)\s*%', self.displayed_discount_raw or '')
                if m and abs(Decimal(m[1].replace(',', '.')) - self.discount_percent) > 1:
                    self.warnings.append('DISPLAYED_DISCOUNT_MISMATCH')
        return self

@dataclass
class Page:
    source: str
    canonical_url: str
    observed_url: str
    title: str
    page_type: str
    raw_text: str
    normalized_text: str
    published_at: date | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    inclusion_reason: str | None = None
    items: list[Item] = field(default_factory=list)
    warnings: list = field(default_factory=list)
    publication_evidence: dict | None = None

    def payload(self):
        result = asdict(self)
        result.pop('observed_url')
        # HTML and source whitespace are raw evidence, not semantic identity.
        result.pop('raw_text')
        if result.get('publication_evidence'):
            result['publication_evidence'].pop('raw_capture_ref',None)
        for item in result['items']:
            item.pop('evidence_html')
        result['items'].sort(key=lambda i: (i['product_url'] or '', i['tab_title'] or '', i['product_name']))
        return result

    def content_hash(self):
        from . import EXTRACTOR_VERSION
        return hashlib.sha256((EXTRACTOR_VERSION+json.dumps(self.payload(), sort_keys=True, ensure_ascii=False, default=str)).encode()).hexdigest()
