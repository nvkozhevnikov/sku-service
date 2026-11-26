from __future__ import annotations

import base64
import hashlib
import io
import re
import warnings
from urllib.parse import urlsplit

from lxml import etree, html
from PIL import Image, ImageOps, UnidentifiedImageError
from .owned_sites import CANONICAL_SITE, OWNED_HOSTS

CHANNELS = ('vk', 'news', 'article')
TAGS = {'p','br','h2','h3','h4','strong','b','em','i','u','s','ul','ol','li',
        'blockquote','table','thead','tbody','tr','th','td','a','img','figure','figcaption','div','span','hr'}
DROP = {'script','style','iframe','object','embed','form','input','button','link','meta','svg','math','template'}
STYLE = {
    'text-align': r'(left|right|center|justify)',
    'font-weight': r'(normal|bold|[1-9]00)',
    'font-style': r'(normal|italic)',
    'max-width': r'(100%|[1-9][0-9]{0,3}px)',
    'width': r'(100%|[1-9][0-9]{0,3}px)',
    'height': r'auto',
    'border-collapse': r'collapse',
    'padding': r'[0-9]{1,2}px',
    'border': r'[0-3]px solid #[0-9a-fA-F]{6}',
}

NEWS_STYLE = STYLE | {
    'width': r'100%', 'max-width': r'100%',
    'box-sizing': r'border-box', 'min-width': r'0',
    'color': r'#[0-9a-f]{3}(?:[0-9a-f]{3})?(?:\s*!important)?',
    'background': r'#[0-9a-f]{3}(?:[0-9a-f]{3})?',
    'background-color': r'#[0-9a-f]{3}(?:[0-9a-f]{3})?',
    'font-size': r'(?:1[2-9]|2[0-8])px', 'line-height': r'1(?:\.[0-9]{1,2})?',
    'margin': r'(?:0|[0-9]{1,2}px)(?: (?:0|[0-9]{1,2}px)){0,3}',
    'padding': r'(?:0|[0-9]{1,2}px)(?: (?:0|[0-9]{1,2}px)){0,3}',
    'display': r'(?:block|inline-block|flex|grid)', 'border-radius': r'[0-9]{1,2}px',
    'overflow-x': r'auto', 'overflow-wrap': r'(?:break-word|anywhere)',
    'text-decoration': r'(?:none|underline)(?:\s*!important)?',
    'text-underline-offset': r'[0-9]px',
    'grid-template-columns': r'minmax\(0,\s*1fr\)',
    'gap': r'[0-9]{1,2}px', 'flex-wrap': r'wrap',
    'align-items': r'(?:center|start|end|stretch)',
    'justify-content': r'(?:space-between|center|start|end)',
    'flex': r'[01] [01] (?:auto|[1-9][0-9]{1,2}px)',
    'border': r'(?:0|[0-3]px solid #[0-9a-f]{6})',
}


def safe_news_link(value):
    if not isinstance(value,str) or len(value)>2000 or re.search(r'[\s\\\x00-\x1f]|%[01][0-9a-f]|%2e|%2f|%5c',value,re.I):return False
    try:
        p=urlsplit(value)
        if p.query or p.fragment or p.username or p.password:return False
        if p.scheme=='tel':return bool(re.fullmatch(r'\+?[0-9()-]{5,25}',p.path))
        if p.scheme=='mailto':return bool(re.fullmatch(r'[a-zA-Z0-9._+-]+@sterbrust\.(?:com|ru)',p.path))
        if p.scheme:
            if p.scheme!='https' or p.hostname not in OWNED_HOSTS or p.port not in (None,443):return False
        elif not value.startswith('/') or value.startswith('//'):return False
        path=p.path
        if any(part in ('.','..') for part in path.split('/')):return False
        return path=='/' or any(path.startswith(prefix) for prefix in ('/catalog/','/news/','/articles/','/contacts/','/brands/','/vendors/','/company/'))
    except ValueError:return False


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def public_url(value: str) -> bool:
    try:
        p = urlsplit(value)
        host = p.hostname or ''
        import ipaddress
        if p.scheme != 'https' or not host or p.username or p.password or p.query or p.fragment:
            return False
        if host == 'localhost' or host.endswith(('.localhost','.local')) or '.' not in host:
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return not re.fullmatch(r'[0-9.]+', host)
    except ValueError:
        return False


def safe_link(value: str) -> bool:
    # No network request is made. Public HTTPS links are ordinary text links only.
    return public_url(value)


def sanitize(raw: str, profile: str = 'default') -> tuple[str, bool]:
    if len(raw.encode('utf-8')) > 2 * 1024 * 1024:
        raise ValueError('HTML больше 2 MiB.')
    if not raw.strip():
        return '', False
    try:
        root = html.fragment_fromstring(raw, create_parent='div', parser=html.HTMLParser(no_network=True))
    except (etree.ParserError, ValueError):
        raise ValueError('Не удалось разобрать HTML.') from None
    removed = False
    for element in list(root.iterdescendants()):
        if element.getparent() is None:
            continue
        tag = element.tag.lower() if isinstance(element.tag, str) else ''
        if profile=='news' and tag and (element.get('hidden') is not None or element.get('aria-hidden','').lower()=='true' or re.search(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)',element.get('style',''),re.I)):
            element.drop_tree()
            removed=True
            continue
        if tag in DROP or not tag:
            element.drop_tree() if hasattr(element, 'drop_tree') else element.getparent().remove(element)
            removed = True
            continue
        if tag not in (TAGS | {'section'} if profile=='news' else TAGS):
            element.drop_tag()
            removed = True
            continue
        for key, value in list(element.attrib.items()):
            keep = False
            if key == 'style':
                pairs = []
                for declaration in value.split(';'):
                    if ':' not in declaration:
                        continue
                    name, val = (x.strip().lower() for x in declaration.split(':', 1))
                    styles = NEWS_STYLE if profile == 'news' else STYLE
                    if name in styles and re.fullmatch(styles[name], val):
                        pairs.append(f'{name}:{val}')
                safe = ';'.join(pairs)
                if safe != value:
                    removed = True
                if safe:
                    element.set(key, safe)
                    keep = True
            elif key in ('title', 'alt'):
                element.set(key, value[:1000])
                keep = True
            elif tag == 'a' and key == 'href' and (safe_news_link(value) if profile=='news' else safe_link(value)):
                keep = True
            elif profile=='news' and key=='class' and re.fullmatch(r'(?:sb-news-|sterbrust-)[a-z0-9-]{1,100}',value):
                keep = True
            elif tag in ('td','th') and key in ('colspan','rowspan') and re.fullmatch(r'[1-9][0-9]?', value):
                keep = True
            elif tag == 'img' and key == 'data-asset-id' and re.fullmatch(r'[0-9a-f]{32}', value):
                keep = True
            if not keep:
                del element.attrib[key]
                removed = True
        if tag == 'img' and not element.get('data-asset-id'):
            element.drop_tree()
            removed = True
    # Raw source is kept separately; this safe HTML is the canonical editable format.
    # root.text must be escaped, as it may originate from &lt;script&gt;.
    from html import escape
    result = escape(root.text or '') + ''.join(html.tostring(e, encoding='unicode', method='html') for e in root)
    return result, removed


def plain(raw: str) -> str:
    if not raw.strip():
        return ''
    return html.fragment_fromstring(raw, create_parent='div').text_content()


def render(raw: str, assets: dict, mode: str, asset_map: dict | None = None, profile: str = 'default') -> str:
    safe, _ = sanitize(raw,profile)
    if not safe:
        return ''
    root = html.fragment_fromstring(safe, create_parent='div')
    if profile=='news' and mode=='preview':
        for link in root.iter('a'):
            if link.get('href','').startswith('/'):link.set('href',CANONICAL_SITE+link.get('href'))
    for img in root.iter('img'):
        aid = img.get('data-asset-id')
        if aid not in assets:
            raise ValueError('В тексте есть фотография, отсутствующая в проекте.')
        asset = assets[aid]
        if mode == 'preview':
            src = 'data:image/jpeg;base64,' + base64.b64encode(asset['jpeg']).decode('ascii')
        elif mode == 'final':
            src = (asset_map or {}).get(aid, '')
            if not public_url(src):
                raise ValueError('Заполните публичные HTTPS-адреса всех фотографий в HTML.')
        else:
            src = '../../media/' + aid + '.jpg'
        img.attrib.pop('data-asset-id', None)
        img.set('src', src)
        img.set('style', 'max-width:100%;height:auto')
        if not img.get('alt'):
            img.set('alt', asset.get('alt', ''))
    from html import escape
    return escape(root.text or '') + ''.join(html.tostring(e, encoding='unicode', method='html') for e in root)


def image_bytes(raw: bytes, max_size=20*1024*1024) -> tuple[bytes, dict]:
    if not raw or len(raw) > max_size:
        raise ValueError('Фотография должна быть не больше 20 MiB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as im:
                fmt = im.format
                if fmt not in ('JPEG','PNG','WEBP') or getattr(im, 'n_frames', 1) != 1:
                    raise ValueError('Нужен статический JPEG, PNG или WebP.')
                if im.width * im.height > 50_000_000:
                    raise ValueError('Изображение больше 50 мегапикселей.')
                im.load()
                source_size = [im.width, im.height]
                fixed = ImageOps.exif_transpose(im).convert('RGBA')
                canvas = Image.new('RGB', fixed.size, 'white')
                canvas.paste(fixed, mask=fixed.getchannel('A'))
                canvas.thumbnail((2400,2400))
                output = io.BytesIO()
                canvas.save(output, format='JPEG', quality=92, optimize=True)
                return output.getvalue(), {'format':fmt,'original_size':source_size,'size':list(canvas.size)}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('Не удалось безопасно прочитать изображение.') from None


def square_jpeg(raw: bytes) -> bytes:
    with Image.open(io.BytesIO(raw)) as im:
        small = ImageOps.contain(im.convert('RGB'), (1200,1200))
        canvas = Image.new('RGB', (1200,1200), 'white')
        canvas.paste(small, ((1200-small.width)//2,(1200-small.height)//2))
        output = io.BytesIO()
        canvas.save(output, format='JPEG', quality=92, optimize=True)
        return output.getvalue()
