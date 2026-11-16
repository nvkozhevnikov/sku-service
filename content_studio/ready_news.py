"""Preserve a supplied finished layout through the same safe news HTML profile."""
from .content import sanitize
from .source_parser import parse_source
from lxml import html


def wants_preserved_news(source,mode):
    if mode not in ('auto','preserve','rewrite'):raise ValueError('Выберите способ подготовки новости.')
    is_html=parse_source(source).html_input
    if mode=='preserve' and not is_html:raise ValueError('Для сохранения оформления вставьте готовый HTML новости.')
    return mode=='preserve' or (mode=='auto' and is_html)


def preserve_news(generated,source,mode,title=''):
    if 'news' not in generated['drafts'] or not wants_preserved_news(source,mode):return generated
    safe,removed=sanitize(source,profile='news')
    if not safe.strip():raise ValueError('В готовом HTML не осталось содержимого для новости.')
    generated['drafts']['news']['body']=safe
    tree=html.fragment_fromstring(safe,create_parent='div')
    lead=next((' '.join(p.text_content().split()) for p in tree.iter('p') if len(' '.join(p.text_content().split()))>70),'')
    if lead:
        generated['facts'][0]['text']='Исходные сведения материала: '+lead
        generated['drafts']['news']['announcement']=lead[:250]
        generated['drafts']['news']['seo_description']=lead[:160]
    if not title.strip():
        heading=next((' '.join(p.text_content().split()) for p in tree.iter('p') if 'font-weight:700' in p.get('style','') and 8<len(p.text_content().strip())<150),'')
        if heading:
            generated['title']=heading
            generated['drafts']['news']['title']=generated['drafts']['news']['seo_title']=heading
    generated['metadata']['preserved_news']=True
    generated['metadata']['news_review']={'model_qa':{},'machine_html':'needs_review','facts':'needs_review',
        'status':'needs_review','issues':['Готовый HTML сохранён без перезаписывания моделью. Проверьте ссылки, факты и предпросмотр.']}
    generated['metadata']['notes']=[n for n in generated['metadata']['notes'] if not n.startswith('Новость сокращена')]
    generated['metadata']['notes'].append('Новость сохранена из готового HTML. Безопасные стили и ссылки сохранены; активный и неподдерживаемый код очищен.')
    return generated
