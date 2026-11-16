"""Source-based channel templates. Deterministic, no LLM claims or invented facts."""
import hashlib
import json
import re
from html import escape
from pathlib import Path

from .content import CHANNELS, public_url
from .source_parser import parse_source, compact_text
from .distribution import detect_tags, options, apply_footer

VERSION = '2026-11-06.3'
PRESETS = json.loads((Path(__file__).parent/'content_presets.json').read_text(encoding='utf-8'))


def blocks(text):
    """Compatibility helper: values and captions now form one semantic block."""
    return [b.text for b in parse_source(text).blocks if b.kind != 'eyebrow' and b.text]


def sentence_excerpt(text, limit):
    if len(text) <= limit:
        return text
    short = text[:limit]
    endings = list(re.finditer(r'[.!?](?:\s|$)', short))
    if endings:
        return short[:endings[-1].start()+1]
    return short.rsplit(' ', 1)[0].rstrip(' ,:;') + '…'


def vk_copy(content, heading, config, preset, notes):
    narrative = [b for b in content if b.kind == 'paragraph' and compact_text(b.text)]
    specs = [b for b in content if b.kind == 'spec']
    details = [b for b in content if b.kind == 'detail']
    structured = bool(specs or details or any(b.kind == 'table' for b in content))
    candidates = [compact_text(b.text) for b in narrative[:1 if structured else config['vk_paragraphs']]]
    candidates += ['• ' + b.text for b in specs[:5]]
    candidates += [compact_text(b.text) for b in details[:3]]
    if not specs:
        for table in (b for b in content if b.kind == 'table'):
            candidates += ['• ' + line for line in table.text.splitlines()[:4]]
    if preset == 'promotion':
        # Preserve whole condition blocks; never cut a qualifier off its number.
        terms = [compact_text(b.text) for b in content if b.kind not in ('heading', 'eyebrow') and re.search(r'услов|срок|огранич|скид|цен|руб|%|акци|участ|до\s+\d|при\s+(?:покупке|заказе)', b.text, re.I)]
        selected = list(dict.fromkeys([compact_text(b.text) for b in narrative[:2]] + [b.text for b in specs[:5]] + terms))
        if len(heading + '\n\n' + '\n\n'.join(selected)) > config['vk_limit']:
            notes.append('Условия акции сохранены без обрезки; VK-текст длиннее обычного шаблона. Проверьте его перед отправкой.')
    else:
        selected = []
        size = len(heading)
        for text in dict.fromkeys(candidates):
            if not text or text == heading:
                continue
            if size + 2 + len(text) <= config['vk_limit'] or not selected:
                selected.append(text)
                size += 2 + len(text)
        if size > config['vk_limit']:
            notes.append('Первый смысловой блок длиннее лимита шаблона. Он сохранён целиком, чтобы не оторвать условия от утверждения.')
    if len(selected) < len([b for b in content if b.kind != 'heading']):
        notes.append('VK — краткая версия: выбраны целые смысловые блоки, характеристики идут с подписями. Проверьте полноту условий; исходник сохранён целиком.')
    return '\n\n'.join([heading] + selected)


def generate(source, title='', url='', channels=None, preset='news', reviewed=False, publication_url='', hashtags=None, news_mode='rewrite', include_source_link=True):
    if not isinstance(channels, list) or not channels or not all(isinstance(c, str) and c in CHANNELS for c in channels) or len(channels) != len(set(channels)):
        raise ValueError('Выберите хотя бы один канал: VK, новость или статья.')
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ValueError('Неизвестный шаблон.')
    if not isinstance(url, str):
        raise ValueError('Ссылка источника должна быть строкой.')
    if url and not public_url(url):
        raise ValueError('Ссылка источника должна быть публичным HTTPS URL без секретных параметров.')
    parsed = parse_source(source)
    detected = detect_tags(source, title)
    distribution = options({'publication_url': publication_url, 'hashtags': detected['tags'] if hashtags is None else hashtags})
    content = [b for b in parsed.blocks if b.kind != 'eyebrow']
    if not content:
        raise ValueError('В исходнике нет текста с понятным контекстом.')
    first = next((b for b in content if b.kind in ('paragraph', 'heading')), None)
    heading = str(title).strip() or (sentence_excerpt(first.text, 180) if first else 'Материал из источника')
    if len(heading) > 250:
        raise ValueError('Название длиннее 250 знаков.')
    # Only suppress an actual duplicate title; labelled specs are never title candidates.
    if first and first.text == heading and len(content) > 1:
        content = [b for b in content if b is not first]
    config = PRESETS[preset]
    drafts, notes = {}, list(parsed.notes)
    intro_block = next((b for b in content if b.kind in ('paragraph', 'detail')), content[0])
    intro = sentence_excerpt(compact_text(intro_block.text), 250)
    vk_body = vk_copy(content, heading, config, preset, notes) if 'vk' in channels else ''
    if url and include_source_link and not distribution['publication_url']:
        vk_body += '\n\nПодробнее: ' + url
    for channel in channels:
        d = {'title': heading, 'body': '', 'announcement': intro, 'seo_title': heading, 'seo_description': sentence_excerpt(intro_block.text, 160), 'state': 'DRAFT', 'media': []}
        if channel == 'vk':
            d['body'] = apply_footer(vk_body, current=distribution)
            d['vk_options'] = distribution
        elif channel == 'news':
            selected, pending_heading = [], None
            for item in content:
                if item.kind == 'heading':
                    pending_heading = item
                    continue
                text = compact_text(item.text)
                if not text:
                    continue
                if len([b for b in selected if b.kind != 'heading']) >= config['news_paragraphs']:
                    break
                if pending_heading:
                    selected.append(pending_heading)
                    pending_heading = None
                selected.append(item)
            # Short news flattens tables only as complete labelled rows.
            d['body'] = ''.join(b.markup if b.kind == 'heading' else '<p>' + '<br>'.join(escape(compact_text(line)) for line in b.text.splitlines()) + '</p>' for b in selected)
            if len(selected) < len(content):
                notes.append('Новость сокращена целыми блоками; статья и исходник сохраняют полную структуру.')
        else:
            if parsed.html_input:
                d['body'] = ''.join(b.markup for b in content)
            else:
                d['body'] = content[0].markup
                if len(content) > 1:
                    d['body'] += '<h2>' + escape(config['article_section']) + '</h2>' + ''.join(b.markup for b in content[1:])
            if len(' '.join(b.text for b in content)) < 900:
                notes.append('Для подробной статьи исходник короткий. Шаблон оформляет имеющийся текст и не придумывает анализ или характеристики.')
        if channel != 'vk' and url:
            d['body'] += '<p><a href="' + escape(url, quote=True) + '">Источник материала</a></p>'
        drafts[channel] = d
    from .ready_news import preserve_news
    from .editorial_style import natural_text
    if 'vk' in drafts:
        drafts['vk']['body']=apply_footer(natural_text(vk_body),current=distribution)
    generated={'title': heading, 'drafts': drafts, 'source': {'text': source, 'url': url},
            'facts': [{'text': 'Исходные сведения материала: ' + heading, 'evidence': url or 'Вставленный оператором исходник; оригинал сохранён в проекте.', 'state': 'CONFIRMED' if reviewed is True else 'UNREVIEWED', 'valid_until': ''}],
            'metadata': {'preset': preset, 'template_version': VERSION, 'engine': 'local-source-template', 'channels': channels, 'source_hash': hashlib.sha256(source.encode()).hexdigest(), 'detected_hashtags': detected, 'notes': list(dict.fromkeys(notes))}}
    return preserve_news(generated,source,news_mode,title)
