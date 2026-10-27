"""Bounded, secret-safe exception evidence. No transport or configuration mutation."""
import errno
import re
import ssl
from urllib.error import URLError,HTTPError
from urllib.parse import urlsplit,urlunsplit


def safe_message(value):
    text=str(value)[:8192]
    def url(match):
        try:
            p=urlsplit(match.group(0));host=p.hostname or ''
            if p.port:host+=':'+str(p.port)
            return urlunsplit((p.scheme,host,p.path,'',''))
        except ValueError:return '[REDACTED_URL]'
    text=re.sub(r'[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>"\x27]+',url,text)
    text=re.sub(r'(?im)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=][^\r\n]*','[REDACTED_HEADER]',text)
    text=re.sub(r'''(?ix)(["']?\b(?:password|passwd|pwd|token|access_token|refresh_token|api[_-]?key|secret|sessid|session[_-]?id)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;\}\]]+)''',r'\1[REDACTED]',text)
    return ' '.join(text.split())[:1000]


def exception_evidence(error):
    seen=set()
    def item(exc,depth=0):
        if not isinstance(exc,BaseException):return {'type':type(exc).__name__,'message':safe_message(exc)}
        node={'type':type(exc).__name__,'module':type(exc).__module__,'message':safe_message(exc)}
        if id(exc) in seen:return {**node,'repeated_reference':True}
        seen.add(id(exc))
        for key in ('errno','winerror','verify_code'):
            value=getattr(exc,key,None)
            if type(value) is int:node[key]=value
        if getattr(exc,'verify_message',None):node['verify_message']=safe_message(exc.verify_message)
        if depth>=4:node['chain_truncated']=True;return node
        reason=getattr(exc,'reason',None)
        if reason is not None:node['reason']=item(reason,depth+1)
        if exc.__cause__ is not None:node['cause']=item(exc.__cause__,depth+1)
        elif exc.__context__ is not None:node['context']=item(exc.__context__,depth+1)
        return node
    return {'format':'transport-exception/v1','original_exception':item(error)}


def local_transport_error(error):
    """A robots transport refusal is local; resource/security failures are not."""
    if isinstance(error,HTTPError):return False
    reason=error.reason if isinstance(error,URLError) else error
    if isinstance(reason,BaseException) and reason is not error:return local_transport_error(reason)
    # SSL errno uses its own namespace (SSL_ERROR_SSL=1 is not POSIX EPERM).
    if isinstance(error,ssl.SSLError):return True
    if isinstance(error,PermissionError):return False
    if isinstance(error,OSError) and (error.errno in {errno.ENOSPC,errno.EMFILE,errno.ENFILE,errno.ENOMEM,errno.EACCES,errno.EPERM} or getattr(error,'winerror',None) in (5,10013)):
        return False
    return isinstance(error,(URLError,TimeoutError,ConnectionError,OSError))
