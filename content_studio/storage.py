from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .content import CHANNELS, digest, sanitize


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class Conflict(Exception):
    pass


class Store:
    initialize_schema=True
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root/'media').mkdir(exist_ok=True)
        with self.connection() as c:
            version=c.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1):raise ValueError('Версия локальной базы новее приложения. Используйте соответствующую версию Studio.')
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,title TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(project_id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(project_id,revision));
                CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS composition_runs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.root/'studio.sqlite3', timeout=5)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA synchronous=FULL')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def list(self, search=''):
        with self.connection() as c:
            rows=c.execute('SELECT id,title,revision,updated_at,payload FROM projects ORDER BY updated_at DESC LIMIT 1000').fetchall()
        result=[]
        for r in rows:
            if search.casefold() not in r['title'].casefold():continue
            p=json.loads(r['payload'])
            result.append({'id':r['id'],'title':r['title'],'revision':r['revision'],'updated_at':r['updated_at'],
                           'media_count':len(p['media']),'ready_channels':[k for k,d in p['drafts'].items() if d.get('state')=='APPROVED']})
        return result

    def create(self, title='Новый материал', initial=None, composition_metadata=None):
        p={'id':uid(),'title':title[:250] or 'Новый материал','revision':0,'source':{'url':'','text':''},'facts':[],
           'drafts':{k:{'title':'','body':'','announcement':'','seo_title':'','seo_description':'','state':'DRAFT','media':[]} for k in CHANNELS},
           'media':[], 'asset_map':{}}
        if initial is not None:
            validated=self.validate({**p,**initial},p)
            p={**validated,'id':p['id'],'revision':0}
        with self.connection() as c:
            c.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(p['id'],p['title'],0,packed(p),now()))
            self._revision(c,p)
            self._created(c,p)
            if composition_metadata is not None:c.execute('INSERT INTO composition_runs VALUES(?,?,?,?,?)',(uid(),p['id'],p['revision'],packed(composition_metadata),now()))
        return p

    def _created(self,c,p):
        """Integrated storage records ownership in the same creation transaction."""
        pass

    def composition(self,pid,metadata=None,revision=None):
        with self.connection() as c:
            if metadata is not None:
                c.execute('INSERT INTO composition_runs VALUES(?,?,?,?,?)',(uid(),pid,revision,packed(metadata),now()))
                return metadata
            row=c.execute('SELECT payload FROM composition_runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1',(pid,)).fetchone()
            return json.loads(row[0]) if row else None

    def _revision(self,c,p):
        body=packed(p)
        c.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(p['id'],p['revision'],body,now(),digest(body.encode())))

    def get(self,pid):
        with self.connection() as c:
            r=c.execute('SELECT payload FROM projects WHERE id=?',(pid,)).fetchone()
        if not r:
            raise KeyError('Материал не найден.')
        return json.loads(r[0])

    def save(self,pid,payload,expected,composition_metadata=None):
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT payload,revision FROM projects WHERE id=?',(pid,)).fetchone()
            if not row:raise KeyError('Материал не найден.')
            if row['revision']!=expected:raise Conflict('Материал изменён в другой вкладке. Скопируйте свои правки перед загрузкой новой версии.')
            old=json.loads(row['payload'])
            p=self.validate(payload,old)
            p['id']=pid;p['revision']=expected+1
            c.execute('UPDATE projects SET title=?,revision=?,payload=?,updated_at=? WHERE id=?',(p['title'],p['revision'],packed(p),now(),pid))
            self._revision(c,p)
            if composition_metadata is not None:c.execute('INSERT INTO composition_runs VALUES(?,?,?,?,?)',(uid(),pid,p['revision'],packed(composition_metadata),now()))
        return p

    def validate(self,p,old):
        if not isinstance(p,dict):raise ValueError('Ожидается карточка материала.')
        # Closed schema: unknown input fields cannot sneak credentials into export.
        source=p.get('source',{})
        if not isinstance(source,dict):raise ValueError('Некорректный источник.')
        source={k:str(source.get(k,'')) for k in ('url','text')}
        if any(len(v.encode())>2*1024*1024 for v in source.values()):raise ValueError('Источник больше 2 MiB.')
        facts=p.get('facts',[])
        if not isinstance(facts,list) or len(facts)>200:raise ValueError('Не больше 200 фактов.')
        cleaned=[]
        for f in facts:
            if not isinstance(f,dict):raise ValueError('Некорректный факт.')
            item={k:str(f.get(k,''))[:10000] for k in ('text','evidence','valid_until')}
            item['state']=f.get('state','UNREVIEWED')
            if item['state'] not in ('UNREVIEWED','CONFIRMED','CONFLICT','STALE'):raise ValueError('Некорректный статус факта.')
            if item['valid_until']:
                from datetime import date
                try:date.fromisoformat(item['valid_until'])
                except ValueError:raise ValueError('Укажите срок факта в формате ГГГГ-ММ-ДД.') from None
            cleaned.append(item)
        ids={a['id'] for a in old['media']}
        media=[]
        byid={a['id']:a for a in old['media']}
        incoming=p.get('media',[])
        if not isinstance(incoming,list) or len(incoming)!=len(ids):raise ValueError('Медиатека меняется только через загрузку фотографий.')
        if {a.get('id') for a in incoming}!=ids:raise ValueError('Неизвестное фото.')
        for a in incoming:
            asset=dict(byid[a['id']])
            for field in ('alt','origin','rights'):
                asset[field]=str(a.get(field,''))[:2000]
            media.append(asset)
        drafts={}
        for channel in CHANNELS:
            d=p.get('drafts',{}).get(channel,{})
            body=str(d.get('body',''))
            from .distribution import options, apply_footer
            vk_options=options(d.get('vk_options')) if channel=='vk' and 'vk_options' in d else None
            if vk_options is not None:body=apply_footer(body,old['drafts'][channel].get('vk_options'),vk_options)
            if len(body.encode())>2*1024*1024:raise ValueError('Текст больше 2 MiB.')
            if channel!='vk':
                safe, removed=sanitize(body,profile=channel)
                if removed and not p.get('accept_cleaning'):
                    raise ValueError('HTML содержит неподдерживаемые теги/атрибуты. Сначала примените безопасную версию в режиме HTML.')
                body=safe
            order=d.get('media',[])
            if not isinstance(order,list) or len(order)!=len(set(order)) or not set(order)<=ids:raise ValueError('Некорректный порядок фото.')
            if channel=='vk' and len(order)>10:raise ValueError('В VK-варианте не больше 10 фото.')
            drafts[channel]={k:str(d.get(k,''))[:10000] for k in ('title','announcement','seo_title','seo_description')}
            drafts[channel].update(body=body,media=order,state='DRAFT')
            if vk_options is not None:drafts[channel]['vk_options']=vk_options
            prev=old['drafts'][channel]
            unchanged=all(drafts[channel].get(k)==prev.get(k) for k in ('title','body','media','announcement','seo_title','seo_description')) and cleaned==old['facts'] and media==old['media'] and source==old['source'] and p.get('asset_map',{})==old['asset_map']
            if unchanged and prev.get('state')=='APPROVED':drafts[channel]['state']='APPROVED'
        amap=p.get('asset_map',{})
        if not isinstance(amap,dict):raise ValueError('Некорректная карта адресов.')
        return {'title':str(p.get('title','Новый материал'))[:250] or 'Новый материал','source':source,'facts':cleaned,
                'media':media,'drafts':drafts,'asset_map':{k:str(v)[:2000] for k,v in amap.items() if k in ids}}

    def approve(self,pid,channel,expected):
        if channel not in CHANNELS:raise ValueError('Неизвестный канал.')
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT payload,revision FROM projects WHERE id=?',(pid,)).fetchone()
            if not row:raise KeyError('Материал не найден.')
            if row['revision']!=expected:raise Conflict('Сначала загрузите текущую версию.')
            p=json.loads(row['payload']);d=p['drafts'][channel]
            if not d['body'].strip():raise ValueError('Добавьте текст.')
            if not p['facts'] or any(f['state']!='CONFIRMED' or not f['evidence'].strip() for f in p['facts']):raise ValueError('Подтвердите факты и укажите основания.')
            for f in p['facts']:
                if f['valid_until'] and f['valid_until']<now()[:10]:raise ValueError('Срок действия факта истёк.')
            assets={a['id']:a for a in p['media']}
            import re
            referenced=set(d['media'])|set(re.findall(r'data-asset-id="([0-9a-f]{32})"',d['body']))
            if not referenced<=assets.keys():raise ValueError('В HTML есть неизвестное фото.')
            if any(not assets[x]['rights'].strip() for x in referenced):raise ValueError('Укажите права на выбранные фото.')
            d['state']='APPROVED';p['revision']+=1
            c.execute('UPDATE projects SET revision=?,payload=?,updated_at=? WHERE id=?',(p['revision'],packed(p),now(),pid))
            self._revision(c,p)
        return p

    def media_add(self,pid,raw,jpeg,metadata,expected):
        aid=uid()
        # Complete immutable bytes precede their DB references. Orphan files are harmless after failed CAS.
        for ext,data in (('original',raw),('jpg',jpeg)):
            path=self.root/'media'/f'{aid}.{ext}'
            with path.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT payload,revision FROM projects WHERE id=?',(pid,)).fetchone()
            if not row:raise KeyError('Материал не найден.')
            if row['revision']!=expected:raise Conflict('Материал изменён. Загрузите текущую версию и повторите добавление фото.')
            p=json.loads(row['payload'])
            if len(p['media'])>=30:raise ValueError('Не больше 30 оригиналов в проекте.')
            a={**metadata,'id':aid,'original_hash':digest(raw),'jpeg_hash':digest(jpeg),'alt':'','origin':'','rights':''}
            p['media'].append(a)
            for channel,d in p['drafts'].items():
                if channel!='vk' or len(d['media'])<10:d['media'].append(aid)
                d['state']='DRAFT'
            p['revision']+=1
            c.execute('INSERT INTO media VALUES(?,?,?)',(aid,pid,packed(a)))
            c.execute('UPDATE projects SET revision=?,payload=?,updated_at=? WHERE id=?',(p['revision'],packed(p),now(),pid))
            self._revision(c,p)
        return p

    def assets(self,p):
        return {a['id']:{**a,'jpeg':(self.root/'media'/f"{a['id']}.jpg").read_bytes()} for a in p['media']}

    def history(self,pid):
        with self.connection() as c:
            return [dict(r) for r in c.execute('SELECT revision,created_at,hash FROM revisions WHERE project_id=? ORDER BY revision DESC LIMIT 200',(pid,))]

    def revision(self,pid,number):
        with self.connection() as c:
            r=c.execute('SELECT payload FROM revisions WHERE project_id=? AND revision=?',(pid,number)).fetchone()
        if not r:raise KeyError('Редакция не найдена.')
        return json.loads(r[0])
