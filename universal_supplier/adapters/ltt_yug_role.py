"""Scoped operator-backed YUG role decision; template controls do not prove a product.

Decision: decisions/2026-10-14_LTT_YUG_ID1_EMPTY_CATEGORY.md. The confirmed
entity is deliberately allowlisted: sparse unrelated products are not excluded.
If its saved structural evidence changes, stop for review instead of guessing.
"""
from urllib.parse import urlparse
from .ltt_uss_html import soup, text, source_model

CONFIRMED_CATEGORY_PATH = '/catalog/derevoobrabatyvayuschee-oborudovanie.html'
DECISION_REF = 'decisions/2026-10-14_LTT_YUG_ID1_EMPTY_CATEGORY.md'


class NonProductYugPage(ValueError):
    pass


def confirmed_category_url(url):
    parsed = urlparse(url or '')
    return (parsed.scheme == 'https' and parsed.hostname in {'ltt-yug.ru', 'www.ltt-yug.ru'}
            and parsed.path == CONFIRMED_CATEGORY_PATH and not parsed.query
            and not parsed.fragment and not parsed.username and not parsed.password)


def classify_yug_page(source, source_url=None):
    dom = soup(source)
    canonical = dom.select_one('link[rel="canonical"]')
    url = source_url or (canonical.get('href') if canonical else '')
    if not confirmed_category_url(url):
        return 'PRODUCT_PAGE'
    main = dom.select_one('.wrapperCatalogPage')
    if main is None:
        return 'AMBIGUOUS_PAGE_ROLE'
    title = text(main.select_one('h1'))
    identities = {e.get('data-product') for e in main.select('[data-product]')}
    descriptions = [text(e) for e in main.select('.accPanelReview')]
    gallery = [e.get('href') for e in main.select('[data-fancybox]')]
    params = [(text(e.select_one('.label')), text(e.select_one('.value')))
              for e in main.select('.itemList')]
    parents = [a.get('href') for a in dom.select('.breadcrumb a[href]')]
    signature = (title == 'Деревообрабатывающее оборудование' and source_model(title) is None
                 and identities == {'1'} and descriptions == ['']
                 and gallery == ['/img/noImg.jpg'] and params
                 and all(k == 'Страна происхождения' for k, v in params)
                 and parents == ['/', '/catalog'] and not main.select('a[href],table'))
    return 'CATEGORY_PAGE' if signature else 'AMBIGUOUS_PAGE_ROLE'
