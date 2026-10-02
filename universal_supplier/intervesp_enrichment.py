"""Generic saved Intervesp detail enrichment, not a canonical identity decision."""
from dataclasses import replace
import re
from urllib.parse import urljoin, urlsplit
from lxml import html

from .adapters.intervesp import parse_intervesp_detail
from .intervesp_listing import listing_snapshot, product_url, url_identity, present
from sterbrust_matching.normalization import normalize_brand, normalize_model, model_tokens
from sterbrust_matching.product_identity import classify_product_kind


def text(node): return ' '.join(node.text_content().split())


def content_url(value, base):
    url = urljoin(base, value)
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.hostname in ('localhost', '127.0.0.1')):
        return None
    return url


def enrich_saved_detail(source: str, row: dict, *, source_url: str):
    """Strict contradictions return ValueError/REVIEW, never an invented alias."""
    url = product_url(source_url)
    if url != product_url(row['product_url']): raise ValueError('detail URL differs from pinned listing URL')
    tree = html.fromstring(source)
    titles = tree.xpath('//h1')
    if len(titles) != 1 or not text(titles[0]): raise ValueError('detail H1 is missing/ambiguous')
    name = text(titles[0])
    # Title model candidates are not enough to confirm identity, but a missing
    # exact execution token is enough to refuse a commercial projection.
    expected = present(row.get('model_candidate'))
    if expected and normalize_model(expected) not in model_tokens(name):
        raise ValueError('listing/detail full model or execution contradiction')
    listing_kind = classify_product_kind(row['title'], present(row.get('category'))).product_kind
    detail_kind = classify_product_kind(name, present(row.get('category'))).product_kind
    if listing_kind != 'unknown' and detail_kind != 'unknown' and listing_kind != detail_kind:
        raise ValueError('listing/detail equipment kind contradiction')
    main = tree.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," el_Main ")]')
    if len(main) != 1: raise ValueError('scoped detail product block is missing/ambiguous')
    main = main[0]
    props = []
    for tr in main.xpath('.//*[@id="elTabProp"]//tr'):
        cells = [text(td) for td in tr.xpath('./td')]
        if len(cells) >= 2 and cells[0] and cells[-1]:
            unit = cells[1] if len(cells) == 3 else ''
            props.append((cells[0] + (f' ({unit})' if unit else ''), cells[-1]))
    for prop in main.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," el_Prop ")]'):
        labels = prop.xpath('.//*[contains(@class,"elProp_Name")]')
        values = prop.xpath('.//*[contains(@class,"elProp_Val")]')
        if labels and values and text(labels[0]) and text(values[0]): props.append((text(labels[0]), text(values[0])))
    props = tuple(dict.fromkeys(props))
    brand_values = tree.xpath('//meta[@property="product:brand"]/@content')
    explicit_brands = [v.strip() for v in brand_values if v.strip()]
    explicit_brands += [value for key,value in props if key.lower() in ('бренд','производитель')]
    normalized = {normalize_brand(value) for value in explicit_brands}
    if len(normalized) > 1: raise ValueError('contradictory explicit detail brand evidence')
    brand = explicit_brands[0] if explicit_brands else present(row.get('brand'))
    if brand and present(row.get('brand')) and normalize_brand(brand) != normalize_brand(row['brand']):
        raise ValueError('listing/detail brand contradiction')
    descriptions = main.xpath('.//*[@id="elTabDesc"]')
    description = text(descriptions[0]) if descriptions else ''
    images = tuple(dict.fromkeys(image_url for value in main.xpath('.//*[@itemprop="image"]/@src')
                                if (image_url := content_url(value, url))))
    documents = tuple(dict.fromkeys((text(a), target) for a in main.xpath('.//*[@id="elTabFiles"]//a[@href]')
                                   if (target := content_url(a.get('href'), url))))
    observed = parse_intervesp_detail(source, source_url=url)
    baseline = listing_snapshot(row)
    # Exact content and observed detail price, not guessed site/manufacturer IDs.
    return replace(observed, name=name, supplier_model=expected or observed.supplier_model,
        raw_supplier_model=expected or observed.raw_supplier_model, supplier_external_id=url_identity(url),
        source_brand=brand, description_text=description, technical_properties=props or baseline.technical_properties,
        source_images=images, source_documents=documents,
        enrichment_evidence={'scope':'full_product_detail', 'model_status':'CANDIDATE',
            'description_checked':True, 'characteristics_checked':True, 'images_checked':True, 'documents_checked':True,
            'brand_basis':'explicit_detail_property' if explicit_brands else 'listing_or_unknown',
            'listing_model_basis':row.get('model_candidate_basis'),
            'available_counts':{'properties':len(props),'images':len(images),'documents':len(documents)},
            'source_url':url, 'raw_listing_identity_evidence':row})
