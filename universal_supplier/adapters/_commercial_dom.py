"""Own-product commercial evidence; ambiguity never becomes stock or a price."""
from bs4 import BeautifulSoup
from urllib.parse import urlsplit


def schema_availability(root):
    values=set()
    allowed={'InStock','OutOfStock','PreOrder','BackOrder','LimitedAvailability','Discontinued','InStoreOnly','SoldOut','OnlineOnly'}
    for node in root.select('[itemprop="availability"]'):
        raw=node.get('href') or node.get('content') or node.get_text(' ',strip=True)
        parsed=urlsplit(raw)
        value=parsed.path.rsplit('/',1)[-1] if parsed.hostname in {'schema.org','www.schema.org'} and not parsed.query and not parsed.fragment else raw
        if value in allowed:values.add(value)
    return next(iter(values)) if len(values)==1 else None


def soup(source):
    return BeautifulSoup(source,'html.parser')
