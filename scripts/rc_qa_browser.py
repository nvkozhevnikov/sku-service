"""Real Edge UI acceptance on copied data; only own UI process is stopped."""
import json,os,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from rc_prepare_database import OUT
from rc_runtime import credentials
from playwright.sync_api import sync_playwright

def main():
    blocked=[];checks={};base='http://127.0.0.1:58161'
    with socket.socket() as s:
        s.bind(('127.0.0.1',58161))
    log=(OUT/'browser-ui-server.log').open('wb')
    proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/rc_runtime.py')],cwd=ROOT,stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else (blocked.append(r.request.url),r.abort())[-1])
            for _ in range(60):
                try:page.goto(base+'/login',wait_until='domcontentloaded');break
                except Exception:
                    if proc.poll() is not None:raise RuntimeError('UI process exited; see local log')
                    time.sleep(.2)
            page.locator('input[name=username]').fill('rc-review')
            page.locator('input[name=password]').fill(credentials()['qa_login_password'])
            page.locator('form button').click();page.wait_for_url(base+'/')
            checks['actual_single_login']=page.locator('a[href="/competitor-monitoring"]').count()==1
            for route,name in [('/products','suppliers'),('/competitor-monitoring/news','news'),('/competitor-monitoring/prices','prices'),('/release-jobs','replay')]:
                response=page.goto(base+route,wait_until='networkidle')
                checks[name+'_200']=response.status==200
                checks[name+'_no_backend_error']='База мониторинга сейчас недоступна' not in page.inner_text('body')
                page.screenshot(path=str(OUT/('PANEL_'+name+'.png')),full_page=True)
            page.locator('form:has(input[name="source_key"][value="competitor:metalmaster"]) button').click()
            page.wait_for_url(base+'/release-jobs')
            result=subprocess.run([sys.executable,str(ROOT/'scripts/rc_runtime.py'),'--work-one'],cwd=ROOT,capture_output=True,timeout=30)
            checks['actual_form_enqueue_and_separate_replay']=result.returncode==0 and b'FROZEN_DATABASE_REPLAY' in result.stdout
            page.reload(wait_until='networkidle');page.screenshot(path=str(OUT/'PANEL_replay_after.png'),full_page=True)
            checks['external_requests_zero']=not blocked
            browser.close()
    finally:
        proc.terminate();proc.wait(timeout=15);log.close()
    receipt={'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'blocked_external_requests':blocked,'ui_stopped_after_qa':True}
    (OUT/'BROWSER_QA.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8');print(json.dumps(receipt,indent=2));assert all(checks.values())
if __name__=='__main__':main()
