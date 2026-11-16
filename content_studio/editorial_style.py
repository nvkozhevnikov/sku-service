"""Plain prose normalization only; never edit URLs, CSS, model hyphens or time."""
import re
from lxml import html

NATURAL_PROSE = ('Пиши естественно и конкретно, без канцелярита и рекламных штампов: '
    'не используй «идеальное решение», «выведите на новый уровень», «не упустите уникальную возможность». '
    'В повествовании не используй длинное тире —/– и двоеточие. Перестраивай фразы. '
    'Короткий дефис внутри модели, адрес ссылки и время не изменяй.')


def natural_text(text):
    parts=re.split(r'(https?://[^\s<>]+|mailto:[^\s<>]+|\b\d+(?::\d+)+(?:[.,]\d+)?\b)',text)
    for i in range(0,len(parts),2):
        parts[i]=re.sub(r'\s*[—–]\s*',' - ',parts[i])
        parts[i]=re.sub(r':\s*',' ',parts[i])
        parts[i]=re.sub(r'[ \t]{2,}',' ',parts[i])
    return ''.join(parts)


def natural_html(raw):
    root=html.fragment_fromstring(raw,create_parent='div')
    for node in root.iter():
        if node.text:node.text=natural_text(node.text)
        if node.tail:node.tail=natural_text(node.tail)
    from html import escape
    return escape(root.text or '')+''.join(html.tostring(n,encoding='unicode') for n in root)
