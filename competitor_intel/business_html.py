import json
from pathlib import Path

def render_html(facts):
    display={**facts,'sources':[{**s,'region':(s.get('region') or '').replace(' (source display)',' (по данным сайта)')} for s in facts['sources']]}
    payload=json.dumps(display,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    template=Path(__file__).with_name('business_dashboard.html').read_text(encoding='utf-8')
    # Currency is a normalized field, never a source-name presentation branch.
    for field in ('old_price','new_price','discount_amount'):
        template=template.replace(f'money(i.{field})',f'money(i.{field},i.currency)')
    template=template.replace('money(e.absolute_delta)','money(e.absolute_delta,e.currency)')
    return template.replace('__REPORT_DATA__',payload)
