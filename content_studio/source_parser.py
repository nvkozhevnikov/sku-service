"""Extract complete editorial units from pasted HTML, without interpreting instructions.

No network I/O. Numeric values are only labelled from their own DOM card/table,
never from a guessed unit dictionary or a nearby unrelated paragraph.
"""
from dataclasses import dataclass, field
from html import escape
import re

from lxml import html

from .content import sanitize


@dataclass
class SourceBlock:
    kind: str
    text: str
    markup: str = ''


@dataclass
class ParsedSource:
    blocks: list[SourceBlock] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    html_input: bool = False


def clean(text):
    text = re.sub(r'\s+', ' ', text.replace('\xa0', ' ')).strip()
    return re.sub(r'\s+([,.;:!?])', r'\1', text)


def text_of(node):
    return clean(' '.join(node.itertext()))


def numeric_value(text):
    # Only a compact standalone value, not a sentence or a product model.
    number = r'\d+(?:[ \u202f]\d{3})*(?:[.,]\d+)?'
    unit = r'(?:[%°₽]|[A-Za-zА-Яа-яЁё]{1,10}[²³]?(?:/[A-Za-zА-Яа-яЁё]{1,10})?)'
    return bool(re.fullmatch(r'(?:до\s+|от\s+)?[±+−]?' + number + r'(?:\s*[–—×x-]\s*' + number + r')*(?:\s*' + unit + r')?\*?', text))


def block(kind, text):
    text = clean(text)
    return SourceBlock(kind, text, ('<h2>' + escape(text) + '</h2>') if kind == 'heading' else '<p>' + escape(text) + '</p>')


def table_block(node, notes):
    rows = [[text_of(c) for c in row if c.tag in ('td', 'th')] for row in node.xpath('./tr|./thead/tr|./tbody/tr|./tfoot/tr')]
    rows = [row for row in rows if row]
    if not rows:
        return None
    first_row = node.xpath('./tr|./thead/tr|./tbody/tr|./tfoot/tr')[0]
    has_header = first_row.getparent().tag == 'thead' or (len(first_row) > 1 and all(c.tag == 'th' for c in first_row))
    headers, data = (rows[0], rows[1:]) if has_header else ([], rows)
    lines = []
    if node.xpath('.//*[@colspan or @rowspan]'):
        notes.append('Таблица с объединёнными ячейками сохранена для статьи; её значения не сокращались в VK без ручной проверки.')
    else:
        for cells in data:
            if len(cells) < 2 or not cells[0]:
                continue
            if len(cells) == 2 and not headers:
                lines.append(cells[0] + ': ' + cells[1])
            else:
                values = [(headers[i] if i < len(headers) and headers[i] else 'Колонка ' + str(i + 1)) + ': ' + cells[i] for i in range(1, len(cells))]
                lines.append(cells[0] + ' — ' + '; '.join(values))
    return SourceBlock('table', '\n'.join(lines), html.tostring(node, encoding='unicode', with_tail=False))


def parse_source(source):
    if not isinstance(source, str) or not source.strip():
        raise ValueError('Вставьте исходную новость или текст.')
    if len(source.encode()) > 2 * 1024 * 1024:
        raise ValueError('Исходник больше 2 MiB.')
    result = ParsedSource()
    if not re.search(r'</?[a-zA-Z][^>]*>', source):
        result.blocks = [block('paragraph', p) for p in re.split(r'\n\s*\n|\r?\n', source) if p.strip()]
    else:
        result.html_input = True
        root = html.fragment_fromstring(source, create_parent='div', parser=html.HTMLParser(no_network=True))
        # Hidden/source-only content must not become editorial copy after stripping CSS.
        for el in list(root.iterdescendants()):
            if not isinstance(el.tag, str) or el.getparent() is None:
                continue
            if 'hidden' in el.attrib or el.get('aria-hidden', '').lower() == 'true' or re.search(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important)?\s*(?:;|$)', el.get('style', ''), re.I):
                el.drop_tree()
            elif el.tag.lower() == 'h1':
                el.tag = 'h2'
        safe, _ = sanitize(html.tostring(root, encoding='unicode'))
        root = html.fragment_fromstring(safe, create_parent='div', parser=html.HTMLParser(no_network=True))

        def walk(node):
            tag = node.tag
            if tag == 'table':
                item = table_block(node, result.notes)
                if item:
                    result.blocks.append(item)
                return
            if tag in ('p', 'li', 'blockquote', 'h2', 'h3', 'h4'):
                text = text_of(node)
                if text:
                    result.blocks.append(block('heading' if tag.startswith('h') else 'paragraph', text))
                return
            children = [c for c in node if isinstance(c.tag, str) and text_of(c)]
            # A complete value/label card. The parent scope prevents cross-card guesses.
            if tag == 'div' and len(children) == 2 and not clean(node.text or '') and not any(clean(c.tail or '') for c in children):
                first, second = children
                a, b = text_of(first), text_of(second)
                if first.tag in ('strong', 'b', 'div', 'span') and second.tag in ('span', 'div', 'p') and not first.xpath('.//p|.//table|.//div') and numeric_value(a) and len(b) <= 130 and re.search(r'[A-Za-zА-Яа-яЁё]', b) and not numeric_value(b):
                    result.blocks.append(block('spec', b.rstrip(':') + ': ' + a))
                    return
                # Layout rows and explanatory cards retain their own label and caveats.
                if first.tag in ('div', 'strong', 'b', 'h2', 'h3', 'h4') and second.tag in ('div', 'p') and not first.xpath('.//p|.//table|.//div') and len(a) <= 120 and len(b) > 30 and not re.search(r'[.!?]', a) and not second.xpath('.//table|.//div|.//h2|.//h3|.//h4'):
                    result.blocks.append(block('detail', a.rstrip(':') + ': ' + b))
                    return
            # Keep adjacent inline fragments together; split at semantic/layout boundaries.
            buffer = [node.text or '']
            def flush():
                text = clean(' '.join(buffer))
                if text:
                    result.blocks.append(block('paragraph', text))
                buffer.clear()
            for child in node:
                if child.tag in ('div', 'p', 'li', 'ul', 'ol', 'blockquote', 'h2', 'h3', 'h4', 'table', 'figure'):
                    flush()
                    walk(child)
                elif child.tag == 'br':
                    buffer.append(' ')
                elif isinstance(child.tag, str):
                    buffer.append(text_of(child))
                buffer.append(child.tail or '')
            flush()
        walk(root)
    # Orphan numbers are not assigned an invented meaning.
    filtered = []
    for item in result.blocks:
        if numeric_value(item.text):
            result.notes.append('Значение без подписи пропущено: ' + item.text + '. Проверьте исходник.')
        else:
            filtered.append(item)
    result.blocks = filtered
    if result.blocks:
        first = result.blocks[0]
        letters = ''.join(c for c in first.text if c.isalpha())
        if len(result.blocks) > 1 and letters.isupper() and re.fullmatch(r'(?:ОБНОВЛЕНИЕ МОДЕЛЬНОГО РЯДА|НОВОСТ[ЬИ]|АНОНС|ОБЗОР|АКЦИЯ)', first.text):
            first.kind = 'eyebrow'
    return result


def compact_text(text):
    """Remove layout navigation only; never drop technical/safety qualification."""
    text = re.sub(r'(?:^|(?<=[.!?])\s+)Ниже\s*(?:[—–:-]\s*)?(?:подробное\s+)?(?:сравнение|таблица|подробности)\b[^.!?]*[.!?]?', ' ', text, flags=re.I)
    text = re.sub(r'(?:^|(?<=[.!?])\s+)На узком экране таблица прокручивается горизонтально\.?', ' ', text, flags=re.I)
    return clean(text)
