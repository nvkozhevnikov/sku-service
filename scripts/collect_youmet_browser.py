"""Public DOM rendered by ordinary Edge. HTTP status preserved separately."""
import json,hashlib,time
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit,urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from competitor_intel.transport import USER_AGENT,challenge
from competitor_intel.model import canonical,parse_date
root=Path('reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12/audit/youmet')
result={'source':'youmet','captures':[],'errors':[],'requests':[]};seen=set();queue=[('https://youmet.ru/','HOMEPAGE_BLOCK')]
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True);context=browser.new_context(user_agent=USER_AGENT);page=context.new_page()
    def route(r):
        u=urlsplit(r.request.url)
        if u.netloc=='youmet.ru' and u.path.startswith(('/personal/','/auth/','/api/','/bitrix/admin/')):r.abort()
        elif r.request.is_navigation_request() and u.netloc!='youmet.ru':r.abort()
        else:r.continue_()
    page.route('**/*',route)
    while queue and len(seen)<25:
        url,kind=queue.pop(0)
        if url in seen:continue
        seen.add(url)
        try:
            if len(seen)>1:time.sleep(5)
            response=page.goto(url,wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(5000)
            html=page.content();s=BeautifulSoup(html,'html.parser')
            if challenge(html) or not s.select_one('h1') or urlsplit(page.url).netloc!='youmet.ru':raise RuntimeError('PUBLIC_DOM_NOT_ACCESSIBLE')
            # A normal rendered shop may retain 403 on its initial HTTP document.
            # Keep that fact; verify public business DOM, never solve challenges.
            if s.title and any(t in s.title.get_text().lower() for t in ['captcha','проверка','sitepatrol']):raise RuntimeError('CAPTCHA_BLOCKED')
            digest=hashlib.sha256(html.encode()).hexdigest();ref=root/(digest+'.browser.html');ref.write_text(html,encoding='utf-8')
            cap={'url':page.url,'kind':kind,'fetch_status':'BROWSER_FETCH_OK','http_status':response.status if response else None,'robots_disallowed':None,'observed_at':datetime.now(timezone.utc).isoformat(),'raw_capture_ref':str(ref),'raw_hash':digest}
            result['captures'].append(cap);result['requests'].append({'url':url,'http_status':cap['http_status'],'fetch_status':cap['fetch_status']})
            if kind=='HOMEPAGE_BLOCK':
                for a in s.select('a[href]'):
                    u=canonical(a['href'],page.url)
                    if urlsplit(u).netloc=='youmet.ru' and urlsplit(u).path in ('/akcia/','/about/news/','/blog/'):
                        k={'/akcia/':'PROMOTION_INDEX','/about/news/':'NEWS_INDEX','/blog/':'BLOG_INDEX'}[urlsplit(u).path]
                        if (u,k) not in queue:queue.append((u,k))
            elif kind in ('PROMOTION_INDEX','NEWS_INDEX','BLOG_INDEX'):
                path=urlsplit(url).path
                for a in s.select('a[href]'):
                    u=canonical(a['href'],url)
                    if urlsplit(u).netloc!='youmet.ru' or not urlsplit(u).path.startswith(path) or u==url:continue
                    parent=a
                    for _ in range(3):
                        if parent.parent:parent=parent.parent
                    d=parse_date(parent.get_text(' ',strip=True))
                    if kind=='PROMOTION_INDEX' or d and d.isoformat()>='2026-09-01':
                        k={'PROMOTION_INDEX':'PROMOTION','NEWS_INDEX':'NEWS','BLOG_INDEX':'BLOG'}[kind]
                        if (u,k) not in queue and u not in seen:queue.append((u,k))
            (root/'BROWSER_LIVE_EVIDENCE.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(kind,url,cap['http_status'],flush=True)
        except Exception as e:result['errors'].append({'url':url,'error':str(e) if isinstance(e,RuntimeError) else type(e).__name__});break
    result['pending']=queue
    (root/'BROWSER_LIVE_EVIDENCE.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    context.close();browser.close()
