"""Normal public access; robots informational, private/auth/protection fail closed."""
import re
from urllib.parse import urlsplit,unquote
from urllib.robotparser import RobotFileParser
from .transport import HttpTransport,USER_AGENT
from pathlib import Path

def decode_public_html(data):
    match=re.search(br'<meta[^>]+charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)',data[:10000],re.I)
    encoding=match[1].decode('ascii') if match else 'utf-8'
    return data.decode(encoding,errors='strict')

class SourceTransport(HttpTransport):
    def __init__(self,config,*args,**kwargs):
        super().__init__(*args,**kwargs); self.config=config; self.resource_gaps=[]; self.tab_gaps=[]
    def permitted(self,url):
        p=urlsplit(url); path=unquote(p.path).lower()
        return p.scheme=='https' and p.netloc in self.config['hosts'] and not p.username and not p.password and not re.match(r'^/(?:admin|bitrix/admin|wp-admin|auth|login|account|personal|api|cart|basket|order)(?:/|$)',path)
    def get(self,url,check_robots=True):
        cap=super().get(url,False)
        cap.html=decode_public_html(Path(cap.raw_ref).read_bytes())
        return cap
    def prepare(self):
        url=self.config['base_url']+'robots.txt'
        cap=super().get(url,False); rp=RobotFileParser();rp.parse(cap.html.splitlines())
        for host in self.config['hosts']: self.robots[host]=rp
        return cap
    def robots_disallowed(self,url):
        return not self.robots[urlsplit(url).netloc].can_fetch(USER_AGENT,url) if urlsplit(url).netloc in self.robots else None
