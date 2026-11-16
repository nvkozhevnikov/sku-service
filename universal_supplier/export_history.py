"""Explicit immutable historical downloads. Never an import/current feed alias."""
import hashlib,json,re
from pathlib import Path
from xml.etree import ElementTree as ET
from .control_plane.source_hourly import atomic_text

def register(folder,raw,*,label,source):
    tree=ET.fromstring(raw)
    if tree.tag not in ('sterbrust_preview','yml_catalog'):raise ValueError('HISTORY_XML_FORMAT')
    root=Path(folder);directory=root/'history';directory.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(raw).hexdigest();path=directory/(key+'.xml')
    if path.exists() and path.read_bytes()!=raw:raise ValueError('HISTORY_IMMUTABLE_CONFLICT')
    if not path.exists():
        # UTF8 is the project's export encoding; bytes must survive roundtrip.
        text=raw.decode('utf-8');assert text.encode('utf-8')==raw
        atomic_text(path,text)
    index_path=root/'history_index.json';items=json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    items[key]={'id':key,'sha256':key,'label':label,'source':str(source),'generated_at':tree.get('generated_at') or tree.get('date'),
        'count':len(tree.findall('product')) if tree.tag=='sterbrust_preview' else len(tree.findall('.//offer')),
        'kind':tree.get('kind','stage5_yml'),'status':'HISTORICAL_NOT_CURRENT','import_allowed':False}
    atomic_text(index_path,json.dumps(items,ensure_ascii=False));return items[key]

def read(folder,key):
    if not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('HISTORY_ID_INVALID')
    root=Path(folder);items=json.loads((root/'history_index.json').read_text(encoding='utf-8'))
    if key not in items:raise KeyError('HISTORY_NOT_FOUND')
    raw=(root/'history'/(key+'.xml')).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=key:raise ValueError('HISTORY_CHECKSUM_CHANGED')
    return raw

def listing(folder):
    p=Path(folder)/'history_index.json'
    return list(json.loads(p.read_text(encoding='utf-8')).values()) if p.exists() else []
