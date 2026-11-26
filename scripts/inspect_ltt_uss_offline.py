"""Offline diagnostic; never performs HTTP or database operations."""
from pathlib import Path
import sys
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding='utf-8')
raw = Path(__file__).resolve().parents[1] / 'reports/LTT_USS_INTEGRATION_2026-10-13/AUDIT/RAW'
for path in sorted(raw.glob('*__*.html')):
    if '__home' in path.name:
        continue
    soup = BeautifulSoup(path.read_bytes().decode('cp1251' if path.name.startswith('uss') else 'utf-8'), 'html.parser')
    print(path.name)
    if path.name.startswith('ltt'):
        h = soup.find('h1')
        print('parents', [(x.name, x.get('class')) for x in list(h.parents)[:6]])
        for e in soup.select('.product_price,.product_price_no_price,.prices,[data-product],form.form-horizontal.ms2_form')[:3]:
            print(str(e)[:400])
    else:
        for row in soup.select('tr'):
            cells = row.find_all(['td', 'th'], recursive=False)
            if cells and any(x in cells[0].get_text(' ', strip=True).lower() for x in ('цена', 'модель', 'наличие', 'номер арт')):
                print([c.get_text(' ', strip=True) for c in cells], row.find_parent('table').get('class'))
