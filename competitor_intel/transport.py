"""One stable browser identity, sequential jitter, bounded retries, fail closed."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import hashlib
import json
import random
import time
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
import requests
from .model import SOURCES

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'

class SourceBlocked(RuntimeError): pass

@dataclass
class Capture:
    url: str
    final_url: str
    html: str
    raw_ref: str
    raw_hash: str
    fetched_at: datetime
    transport: str
    variants: list = field(default_factory=list)

def challenge(html):
    from bs4 import BeautifulSoup
    s = BeautifulSoup(html, 'html.parser')
    title = s.title.get_text(' ', strip=True).lower() if s.title else ''
    # A SmartCaptcha footer/script on a working shop is not a challenge.
    return any(t in title for t in ('captcha','access denied','just a moment','доступ ограничен','проверка браузера'))

class HttpTransport:
    def __init__(self, capture_dir, sleeper=time.sleep, rng=None):
        self.directory = Path(capture_dir); self.directory.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session(); self.session.trust_env = False
        self.session.headers['User-Agent'] = USER_AGENT
        self.sleep = sleeper; self.rng = rng or random.SystemRandom()
        self.started = False; self.events = []; self.robots = {}; self.blocked_hosts = set()

    def pace(self):
        if self.started: self.sleep(self.rng.uniform(3, 7))
        self.started = True

    def permitted(self, url):
        return urlsplit(url).scheme == 'https' and urlsplit(url).netloc in {urlsplit(s['base_url']).netloc for s in SOURCES.values()}

    def get(self, url, check_robots=True):
        if not self.permitted(url): raise ValueError('URL outside exact source allowlist')
        host = urlsplit(url).netloc
        if host in self.blocked_hosts: raise SourceBlocked('Source stopped after protection response')
        if check_robots and host not in self.robots:
            cap = self.get('https://'+host+'/robots.txt', False)
            rp = RobotFileParser(); rp.parse(cap.html.splitlines()); self.robots[host] = rp
        if check_robots and not self.robots[host].can_fetch(USER_AGENT, url):
            raise SourceBlocked('ROBOTS_EXCLUDED')
        current = url
        for attempt in range(3):
            self.pace()
            observed = datetime.now(timezone.utc)
            try:
                r = self.session.get(current, timeout=35, allow_redirects=False)
                r.encoding = 'utf-8'
                event = {'url': current, 'status': r.status_code, 'attempt': attempt+1, 'observed_at': observed.isoformat()}
                self.events.append(event)
                raw_hash = hashlib.sha256(r.content).hexdigest()
                ref = self.directory / (raw_hash+'.html'); ref.write_bytes(r.content)
                event['raw_capture_ref'] = str(ref); event['raw_hash'] = raw_hash
                if r.status_code == 403 or challenge(r.text):
                    self.blocked_hosts.add(host); raise SourceBlocked('HTTP403_OR_CHALLENGE')
                if r.status_code == 429:
                    delay = r.headers.get('Retry-After')
                    try: delay = float(delay)
                    except (TypeError, ValueError):
                        try: delay = max(0, (parsedate_to_datetime(delay)-observed).total_seconds())
                        except (TypeError, ValueError, OverflowError): delay = 10*(attempt+1)
                    if delay > 60 or attempt == 2: self.blocked_hosts.add(host); raise SourceBlocked('HTTP429_DEFERRED')
                    self.sleep(max(3, delay)); continue
                if 500 <= r.status_code < 600 and attempt < 2:
                    self.sleep(8*(attempt+1)); continue
                if r.status_code in (301,302,303,307,308):
                    current = urljoin(current, r.headers.get('Location',''))
                    if not self.permitted(current): raise SourceBlocked('REGION_OR_HOST_REDIRECT')
                    if check_robots and not self.robots[host].can_fetch(USER_AGENT, current): raise SourceBlocked('REDIRECT_ROBOTS_EXCLUDED')
                    continue
                r.raise_for_status()
                return Capture(url, current, r.text, str(ref), raw_hash, observed, 'HTTP')
            except requests.RequestException as e:
                if self.events and self.events[-1]['url'] == current: self.events[-1]['error'] = type(e).__name__
                else: self.events.append({'url':current,'error':type(e).__name__,'attempt':attempt+1})
                if isinstance(e, requests.HTTPError) and r.status_code < 500: raise
                if attempt == 2: raise
                self.sleep(8*(attempt+1))
        raise SourceBlocked('RETRIES_EXHAUSTED')

class BrowserTransport:
    """Ordinary installed Edge; no stealth, auth, proxy or protection bypass.

    Explicit operator choice only after HTTP/robots preflight. All site traffic is
    source/browser traffic. Abort off-domain documents; normal public assets load.
    """
    def __init__(self, http): self.http = http

    def get(self, url):
        if not self.http.permitted(url): raise ValueError('URL outside exact source allowlist')
        host = urlsplit(url).netloc
        if host in self.http.blocked_hosts: raise SourceBlocked('Protected source, browser fallback forbidden')
        if host not in self.http.robots: self.http.get('https://'+host+'/robots.txt',False)
        if host not in self.http.robots: raise SourceBlocked('ROBOTS_PREFLIGHT_REQUIRED')
        if not self.http.robots[host].can_fetch(USER_AGENT,url): raise SourceBlocked('ROBOTS_EXCLUDED')
        from playwright.sync_api import sync_playwright
        self.http.pace()
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()
            protected = []
            page.on('response', lambda r: protected.append(r.status) if urlsplit(r.url).netloc == host and r.status in (403,429) else None)
            def route(req):
                if req.request.is_navigation_request() and urlsplit(req.request.url).netloc != host:
                    req.abort()
                else: req.continue_()
            page.route('**/*', route)
            try:
                response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(5000)
                # Trigger lazy current blocks only. No catalog navigation.
                for el in page.locator('.promo-slider').all():
                    el.scroll_into_view_if_needed(); page.wait_for_timeout(3500)
                html = page.content()
                if protected or not response or response.status >= 400 or challenge(html):
                    self.http.blocked_hosts.add(host); raise SourceBlocked('BROWSER_PROTECTION_STOP')
                if urlsplit(page.url).netloc != host: raise SourceBlocked('REGION_OR_HOST_REDIRECT')
                observed = datetime.now(timezone.utc)
                raw_hash = hashlib.sha256(html.encode()).hexdigest()
                ref = self.http.directory / (raw_hash+'.browser.html'); ref.write_text(html,encoding='utf-8')
                self.http.events.append({'url':url,'status':response.status,'transport':'BROWSER','raw_capture_ref':str(ref),'raw_hash':raw_hash,'observed_at':observed.isoformat()})
                page.screenshot(path=str(ref.with_suffix('.png')), full_page=True)
                capture = Capture(url,page.url,html,str(ref),raw_hash,observed,'BROWSER')
                # Capture each visible seasonal tab with ordinary UI interactions.
                # Keep every original DOM snapshot; never synthesize source HTML.
                if urlsplit(url).path == '/' and host == 'nnov.kuvalda.ru':
                    seasonal = page.locator('.section').filter(has=page.locator('.section__title',has_text='Готовимся к зиме'))
                    if seasonal.count() == 1:
                        buttons = seasonal.locator('.alt-tabs__item')
                        for idx in range(1,buttons.count()):
                            self.http.sleep(self.http.rng.uniform(3,7))
                            buttons.nth(idx).click(timeout=10000)
                            page.wait_for_timeout(4000)
                            snapshot = page.content()
                            if protected or challenge(snapshot):
                                self.http.blocked_hosts.add(host); raise SourceBlocked('BROWSER_TAB_PROTECTION_STOP')
                            tab_hash = hashlib.sha256(snapshot.encode()).hexdigest()
                            tab_ref = self.http.directory / (tab_hash+'.browser-tab.html'); tab_ref.write_text(snapshot,encoding='utf-8')
                            capture.variants.append(Capture(url,page.url,snapshot,str(tab_ref),tab_hash,datetime.now(timezone.utc),'BROWSER_TAB'))
                        manifest = {'initial':str(ref),'initial_hash':raw_hash,'tabs':[{'ref':v.raw_ref,'hash':v.raw_hash,'observed_at':v.fetched_at.isoformat()} for v in capture.variants]}
                        manifest_ref = self.http.directory / (raw_hash+'.manifest.json')
                        manifest_ref.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
                        capture.raw_ref = str(manifest_ref)
                        capture.raw_hash = hashlib.sha256(manifest_ref.read_bytes()).hexdigest()
                return capture
            finally: context.close(); browser.close()
