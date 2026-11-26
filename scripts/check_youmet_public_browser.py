"""One ordinary Edge visit; no retry, identity change, CAPTCHA or protection workaround."""
import json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from playwright.sync_api import sync_playwright
from competitor_intel.transport import USER_AGENT,challenge
root=Path('reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12/audit/youmet')
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    context=browser.new_context(user_agent=USER_AGENT)
    page=context.new_page()
    responses=[]
    page.on('response',lambda r:responses.append({'url':r.url,'status':r.status}) if r.request.is_navigation_request() else None)
    try:
        response=page.goto('https://youmet.ru/',wait_until='domcontentloaded',timeout=45000)
        page.wait_for_timeout(5000)
        html=page.content();digest=hashlib.sha256(html.encode()).hexdigest();ref=root/(digest+'.browser.html');ref.write_text(html,encoding='utf-8')
        status='BROWSER_FETCH_OK' if response and response.status<400 and not challenge(html) and page.locator('h1').count() else 'HTTP_BLOCKED'
        result={'source':'youmet','url':page.url,'fetch_status':status,'robots_disallowed':None,'observed_at':datetime.now(timezone.utc).isoformat(),'raw_capture_ref':str(ref),'raw_hash':digest,'document_responses':responses,'title':page.title(),'ordinary_browser_attempts':1,'evasion':False}
        page.screenshot(path=str(root/'PUBLIC_BROWSER.png'),full_page=True)
    except Exception as e:result={'source':'youmet','fetch_status':'ERROR','error':type(e).__name__,'ordinary_browser_attempts':1,'evasion':False}
    finally:context.close();browser.close()
(root/'PUBLIC_BROWSER.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
