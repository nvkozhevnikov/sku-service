"""News-specific input context and conservative checks adapted from the supplied pack."""
import re
from urllib.parse import urlsplit
from lxml import html

from .content import safe_news_link


class NewsContentError(ValueError):
    pass


def news_links(source,extra=()):
    links = ['/contacts/']
    try:
        root = html.fragment_fromstring(source, create_parent='div', parser=html.HTMLParser(no_network=True))
        for a in root.iter('a'):
            href = a.get('href','')
            if safe_news_link(href) and href not in links:links.append(href)
    except (ValueError,TypeError):pass
    for href in extra:
        if safe_news_link(href) and href not in links:links.append(href)
    return links[:100]


def news_review(raw, draft, allowed_links):
    root = html.fragment_fromstring(raw,create_parent='div',parser=html.HTMLParser(no_network=True))
    issues=[]
    allowed_paths={urlsplit(x).path for x in allowed_links}
    for node in root.iterdescendants():
        tag=node.tag if isinstance(node.tag,str) else ''
        if tag in ('script','style','iframe','object','embed','form','input','button','link','meta','svg','math','template','h1'):
            raise NewsContentError('Ответ новости содержит запрещённый HTML; исходник сохранён.')
        if tag=='img':raise NewsContentError('ИИ не должен вставлять неподтверждённые изображения в новость.')
        if tag=='a':
            href=node.get('href','')
            if not safe_news_link(href) or urlsplit(href).path not in allowed_paths:
                raise NewsContentError('В новости есть неподтверждённая или внешняя ссылка. Добавьте точный адрес в исходник.')
        if re.search(r'\b(?:toc-container|article-toc|table-of-contents|contents-list)\b',node.get('class',''),re.I):
            raise NewsContentError('В новости не должно быть собственного оглавления.')
        style=node.get('style','').lower()
        if re.search(r'width\s*:\s*calc|margin-(left|right)\s*:\s*-|position\s*:\s*(?:absolute|fixed)|border-left[^;]*#f2722c',style):
            raise NewsContentError('В новости есть небезопасное расширение блока или запрещённый декоративный стиль.')
        if tag=='table' and not any(p.get('style','').replace(' ','').find('overflow-x:auto')>=0 for p in node.iterancestors()):
            issues.append('Проверьте горизонтальную прокрутку таблицы на мобильном экране.')
    if re.search('Изображение из медиабиблиотеки Sterbrust',root.text_content(),re.I):issues.append('Удалите служебную подпись изображения.')
    qa=draft.get('qa',{})
    if not isinstance(qa,dict) or any(qa.get(k,'needs_review') not in ('pass','needs_review') for k in ('facts','editorial','html_visual')):
        raise NewsContentError('Некорректная самопроверка новости в ответе ИИ.')
    reported=draft.get('issues',[])
    if not isinstance(reported,list) or len(reported)>30 or any(not isinstance(x,str) or len(x)>1000 for x in reported):
        raise NewsContentError('Некорректный список замечаний к новости.')
    for url in re.findall(r'https?://[^\s<>"\x27]+',raw):
        if not safe_news_link(url) or urlsplit(url).path not in allowed_paths:
            raise NewsContentError('В новости есть неподтверждённый URL.')
    return {'model_qa':{k:qa.get(k,'needs_review') for k in ('facts','editorial','html_visual')},
            'issues':list(dict.fromkeys(issues+reported))[:30], 'machine_html':'needs_review' if issues else 'pass',
            'facts':'needs_review','status':'needs_review'}
