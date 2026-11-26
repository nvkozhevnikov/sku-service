"""Persistent prepare/confirm/dispatch; only explicit intent confirmation writes to VK."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from threading import RLock

from .content import square_jpeg
from .storage import Conflict, now, packed, uid
from .vk_transport import VkClient,VkError,WindowsVault
from .vk_setup import user_token_input, photo_setup

TARGET=68800619


class VkService:
    def __init__(self,store,client=None,vault=None):
        self.store=store;self.client=client or VkClient();self.vault=vault or WindowsVault(store.root/'credentials');self.lock=RLock()
        if store.initialize_schema:
            with store.connection() as c:
                c.executescript('''
                  CREATE TABLE IF NOT EXISTS vk_settings(singleton INTEGER PRIMARY KEY CHECK(singleton=1),payload TEXT NOT NULL);
                  INSERT OR IGNORE INTO vk_settings VALUES(1,'{}');
                  CREATE TABLE IF NOT EXISTS vk_intents(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,state TEXT NOT NULL,expires REAL NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL);
                  CREATE TABLE IF NOT EXISTS vk_steps(id INTEGER PRIMARY KEY,intent_id TEXT NOT NULL,stage TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
                  CREATE UNIQUE INDEX IF NOT EXISTS vk_single_dispatch ON vk_steps(intent_id) WHERE stage='dispatch_claim';
                ''')
                # A previous process may have sent a request. Restart must not dispatch it again.
                c.execute("UPDATE vk_intents SET state='UNCERTAIN',result=? WHERE state='SENDING'",(packed({'message':'Процесс прерван. Проверьте результат в VK вручную; повтор запрещён.'}),))
        else:self.store.recover_inflight()

    def _settings(self,c=None):
        if c:return json.loads(c.execute('SELECT payload FROM vk_settings WHERE singleton=1').fetchone()[0])
        with self.store.connection() as conn:return self._settings(conn)

    def _active(self,c):return c.execute("SELECT 1 FROM vk_intents WHERE state IN ('SENDING','UNCERTAIN') LIMIT 1").fetchone() is not None

    def status(self):
        cfg=self._settings();available=lambda ref:bool(ref) and (not hasattr(self.vault,'available') or self.vault.available(ref))
        return {'target_id':TARGET,'community':cfg.get('community'),'checked_at':cfg.get('checked_at'),
                'community_connected':available(cfg.get('community_ref')),'user_connected':available(cfg.get('user_ref')),
                'photo_capability':cfg.get('photo_capability','unknown'),'photo_error':cfg.get('photo_error'),
                'text_capability':cfg.get('text_capability','unknown'),'read_capability':cfg.get('read_capability','unknown'),
                'photo_setup':photo_setup(cfg if available(cfg.get('user_ref')) else {**cfg,'user_ref':None}),
                'reconnect_required':bool(cfg.get('community_ref') or cfg.get('user_ref')) and not (available(cfg.get('community_ref')) or available(cfg.get('user_ref'))),
                'note':'Проверка прав не публикует записи. Первый пост требует отдельного подтверждения. Фото не заменяются текстом молча.'}

    def _group(self,result):
        groups=result.get('groups',[]) if isinstance(result,dict) else result if isinstance(result,list) else []
        if len(groups)!=1 or groups[0].get('id')!=TARGET:raise ValueError('Ключ должен соответствовать сообществу sterbrust (68800619).')
        g=groups[0]
        return {k:g.get(k) for k in ('id','name','screen_name','can_post','is_admin') if k in g}

    def connect(self,token,kind='community'):
        if kind not in ('community','user'):raise ValueError('Неизвестный тип ключа.')
        if kind=='user':token=user_token_input(token)
        if not isinstance(token,str):raise ValueError('Нужен ключ VK.')
        token=token.strip()
        if not re.fullmatch(r'[A-Za-z0-9._-]{30,4096}',token):raise ValueError('Некорректный формат ключа.')
        with self.lock:
            with self.store.connection() as c:
                if self._active(c):raise ValueError('Сначала разберите незавершённую отправку. Менять ключ сейчас нельзя.')
            if kind=='community':
                permissions=self.client.call('groups.getTokenPermissions',token)
                if not isinstance(permissions,dict) or int(permissions.get('mask',0))&8192==0:raise ValueError('В ключе сообщества не заявлено право wall.')
                group=self._group(self.client.call('groups.getById',token))
            else:
                permissions=self.client.call('account.getAppPermissions',token)
                if type(permissions) is not int or permissions&8196!=8196:raise ValueError('Пользовательскому ключу нужны права wall и photos.')
                group=self._group(self.client.call('groups.getById',token,{'group_id':TARGET,'fields':'can_post'}))
                if group.get('can_post')!=1:raise ValueError('Не подтверждено право пользователя публиковать в этом сообществе.')
            reference=self.vault.put(token)
            with self.store.connection() as c:
                c.execute('BEGIN IMMEDIATE')
                if self._active(c):raise ValueError('Началась отправка. Замена ключа не применена.')
                cfg=self._settings(c);cfg[kind+'_ref']=reference;cfg['version']=uid();cfg['community']=group;cfg['checked_at']=now()
                cfg['text_capability']='declared';cfg['photo_capability']='unknown';cfg.pop('photo_error',None)
                c.execute('UPDATE vk_settings SET payload=? WHERE singleton=1',(packed(cfg),))
            return self.status()

    def import_desktop(self):
        # Exact operator-named file, never an arbitrary UI supplied path or recursive secret search.
        file=Path('D:/documents/service/desktop/токен от вк.txt')
        if file.stat().st_size>8192:raise ValueError('Файл ключа имеет неожиданный размер.')
        raw=file.read_text(encoding='utf-8-sig').strip();matches=re.findall(r'vk1\.[A-Za-z0-9._-]+',raw)
        return self.connect(matches[0] if len(matches)==1 else raw,'community')

    def probe_photos(self):
        with self.lock:
            cfg=self._settings();ref=cfg.get('user_ref') or cfg.get('community_ref')
            if not ref:raise ValueError('Сначала подключите ключ.')
            try:
                result=self.client.call('photos.getWallUploadServer',self.vault.get(ref),{'group_id':TARGET})
                if not isinstance(result,dict) or not isinstance(result.get('upload_url'),str):raise VkError()
                # Do not persist or expose the signed upload URL. No bytes are uploaded here.
                cfg['photo_capability']='upload_server_verified';cfg.pop('photo_error',None)
            except VkError as e:
                cfg['photo_capability']='blocked' if e.code is not None else 'unknown';cfg['photo_error']=e.code
            cfg['photo_checked_at']=now()
            with self.store.connection() as c:c.execute('UPDATE vk_settings SET payload=? WHERE singleton=1',(packed(cfg),))
            return self.status()

    def _snapshot(self,p,cfg,mode='publish',publish_date=None):
        if mode not in ('publish','schedule_text','publish_with_fallback'):raise ValueError('Неизвестный способ отправки.')
        if mode!='publish' and (type(publish_date) is not int or not time.time()+600<=publish_date<=time.time()+365*86400):
            raise ValueError('Выберите время отложки не раньше чем через 10 минут и не позднее года.')
        d=p['drafts']['vk']
        if d.get('state')!='APPROVED':raise ValueError('Сначала отметьте VK-версию готовой.')
        if not d['body'].strip():raise ValueError('Текст пуст.')
        if len(d['body'])>15000:raise ValueError('Локальный профиль отправки: текст до 15000 знаков.')
        if len(d['media'])>10:raise ValueError('Не больше 10 фото.')
        for f in p['facts']:
            if f['state']!='CONFIRMED' or not f['evidence'] or (f['valid_until'] and f['valid_until']<now()[:10]):raise ValueError('Проверьте факты и сроки перед публикацией.')
        assets={a['id']:a for a in p['media']}
        images=[]
        for aid in d['media']:
            if not assets[aid].get('rights'):raise ValueError('Укажите права на фото.')
            data=square_jpeg((self.store.root/'media'/f'{aid}.jpg').read_bytes())
            images.append({'id':aid,'sha256':hashlib.sha256(data).hexdigest()})
        ref=cfg.get('user_ref') or cfg.get('community_ref')
        if not ref:raise ValueError('Сначала подключите VK.')
        if mode=='publish':
            if images and cfg.get('photo_capability') not in ('upload_server_verified','verified'):raise ValueError('Фото API недоступно. Выберите текст в отложку с подготовленными фотографиями.')
            if images and not cfg.get('user_ref'):raise ValueError('Для прямой отправки фото нужен пользовательский ключ.')
        plan='scheduled_text' if mode=='schedule_text' or (mode=='publish_with_fallback' and images and (not cfg.get('user_ref') or cfg.get('photo_capability') not in ('upload_server_verified','verified'))) else 'wall'
        return {'target_id':TARGET,'project_id':p['id'],'revision':p['revision'],'message':d['body'],'images':images,
                'credential_ref':ref,'connection_version':cfg['version'],'from_group':1,'mode':mode,'publish_date':publish_date if mode!='publish' else None,'delivery_plan':plan}

    def prepare(self,pid,mode='publish',publish_date=None):
        with self.lock:
            cfg=self._settings();p=self.store.get(pid);snap=self._snapshot(p,cfg,mode,publish_date)
            signature=hashlib.sha256(packed(snap).encode()).hexdigest()
            with self.store.connection() as c:
                c.execute('BEGIN IMMEDIATE')
                if self._active(c):raise ValueError('Есть отправка с неясным или незавершённым результатом. Сначала проверьте её в журнале.')
                row=c.execute('SELECT id FROM vk_intents WHERE digest=?',(signature,)).fetchone()
                if row:
                    c.execute("UPDATE vk_intents SET expires=? WHERE id=? AND state='PREPARED' AND expires<?",(time.time()+600,row['id'],time.time()))
                    ident=row['id']
                else:
                    ident=uid();c.execute('INSERT INTO vk_intents VALUES(?,?,?,?,?,?,?,?,?)',
                        (ident,pid,p['revision'],signature,packed(snap),'PREPARED',time.time()+600,'{}',now()))
            return self.get(ident)

    def get(self,ident):
        with self.store.connection() as c:r=c.execute('SELECT * FROM vk_intents WHERE id=?',(ident,)).fetchone()
        if not r:raise KeyError('Попытка не найдена.')
        snap=json.loads(r['payload']);result=json.loads(r['result'])
        return {'id':r['id'],'project_id':r['project_id'],'revision':r['revision'],'digest':r['digest'],'state':r['state'],
                'expires_at':r['expires'],'created_at':r['created_at'],'target_id':TARGET,'message':snap['message'],
                'image_ids':[x['id'] for x in snap['images']],'mode':snap.get('mode','publish'),'publish_date':snap.get('publish_date'),
                'delivery_plan':snap.get('delivery_plan','wall'),'result':result}

    def history(self):
        with self.store.connection() as c:ids=[r[0] for r in c.execute('SELECT id FROM vk_intents ORDER BY created_at DESC LIMIT 100')]
        return [self.get(x) for x in ids]

    def claim(self,ident,digest,acknowledged):
        if acknowledged is not True:raise ValueError('Подтвердите проверку конкретного поста.')
        with self.lock,self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM vk_intents WHERE id=?',(ident,)).fetchone()
            if not r:raise KeyError('Попытка не найдена.')
            if r['state']!='PREPARED':return False
            if r['expires']<time.time():raise ValueError('Подтверждение истекло. Подготовьте новую редакцию.')
            if digest!=r['digest']:raise ValueError('Содержимое подтверждения изменилось.')
            if self._active(c):raise ValueError('Другая попытка ещё не завершена.')
            p=json.loads(c.execute('SELECT payload FROM projects WHERE id=?',(r['project_id'],)).fetchone()[0])
            snap=json.loads(r['payload']);cfg=self._settings(c);current=self._snapshot(p,cfg,snap.get('mode','publish'),snap.get('publish_date'))
            if hashlib.sha256(packed(current).encode()).hexdigest()!=digest:raise Conflict('Материал или подключение изменились. Подготовьте пост заново.')
            c.execute("UPDATE vk_intents SET state='SENDING' WHERE id=?",(ident,))
            return True

    def _step(self,ident,stage,state,payload=None):
        with self.store.connection() as c:c.execute('INSERT INTO vk_steps(intent_id,stage,state,payload,created_at) VALUES(?,?,?,?,?)',(ident,stage,state,packed(payload or {}),now()))

    def _finish(self,ident,state,result):
        with self.store.connection() as c:
            c.execute('UPDATE vk_intents SET state=?,result=? WHERE id=?',(state,packed(result),ident))
            if state=='APPLIED':
                snap=json.loads(c.execute('SELECT payload FROM vk_intents WHERE id=?',(ident,)).fetchone()[0]);cfg=self._settings(c)
                if cfg.get('version')==snap['connection_version']:
                    cfg['text_capability']='verified'
                    if result.get('attachment_count',0):cfg['photo_capability']='verified'
                    cfg['read_capability']='verified' if result.get('verification')=='VERIFIED' else 'unknown'
                    c.execute('UPDATE vk_settings SET payload=? WHERE singleton=1',(packed(cfg),))

    def dispatch(self,ident):
        if hasattr(self.store,'dispatch_guard'):
            with self.store.dispatch_guard(ident) as acquired:
                if acquired:self._dispatch(ident)
        else:self._dispatch(ident)

    def _dispatch(self,ident):
        # Caller must have atomically claimed this immutable snapshot. No implicit resume/retry.
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT payload,state FROM vk_intents WHERE id=?',(ident,)).fetchone()
            if not r or r['state']!='SENDING':return
            claimed=c.execute("INSERT OR IGNORE INTO vk_steps(intent_id,stage,state,payload,created_at) VALUES(?,'dispatch_claim','STARTED','{}',?)",(ident,now())).rowcount
            if not claimed:return
        snap=json.loads(r['payload']);stage='preflight';external_started=False;post_applied=False
        try:
            token=self.vault.get(snap['credential_ref'])
            group=self._group(self.client.call('groups.getById',token,{'group_id':TARGET}))
            attachments=[];delivery=snap.get('delivery_plan','wall')
            for image in ([] if delivery=='scheduled_text' else snap['images']):
                stage='upload_server';self._step(ident,stage,'STARTED')
                try:result=self.client.call('photos.getWallUploadServer',token,{'group_id':TARGET})
                except VkError as e:
                    if e.code==27 and not external_started and snap.get('mode')=='publish_with_fallback':delivery='scheduled_text';break
                    raise
                if not isinstance(result,dict) or not isinstance(result.get('upload_url'),str):raise VkError()
                jpg=square_jpeg((self.store.root/'media'/f"{image['id']}.jpg").read_bytes())
                if hashlib.sha256(jpg).hexdigest()!=image['sha256']:raise ValueError('Изменились байты фото.')
                stage='upload';self._step(ident,stage,'STARTED',{'asset_id':image['id']});external_started=True
                uploaded=self.client.upload(result['upload_url'],jpg)
                stage='save_photo';self._step(ident,stage,'STARTED',{'asset_id':image['id']})
                photo=self.client.call('photos.saveWallPhoto',token,{**uploaded,'group_id':TARGET})
                if not isinstance(photo,list) or len(photo)!=1 or type(photo[0].get('owner_id')) is not int or type(photo[0].get('id')) is not int:raise VkError(uncertain=True)
                photo=photo[0]
                if photo['owner_id']!=-TARGET or photo['id']<=0:raise VkError(uncertain=True)
                attachment=f"photo{photo['owner_id']}_{photo['id']}";attachments.append(attachment)
                self._step(ident,stage,'APPLIED',{'attachment':attachment})
            if delivery=='scheduled_text' and snap['publish_date']<=time.time()+60:raise ValueError('До выбранного времени осталось слишком мало времени. Подготовьте новую отложку.')
            stage='wall_post';self._step(ident,stage,'STARTED');external_started=True
            params={'owner_id':-TARGET,'from_group':1,'message':snap['message'],'attachments':','.join(attachments),'guid':ident}
            if delivery=='scheduled_text':params['publish_date']=snap['publish_date']
            result=self.client.call('wall.post',token,params)
            if not isinstance(result,dict) or type(result.get('post_id')) is not int or result['post_id']<=0:raise VkError(uncertain=True)
            post_applied=True;post_id=result['post_id']
            receipt={'post_id':post_id,'url':f'https://vk.com/wall-{TARGET}_{post_id}','verification':'NOT_CHECKED','attachment_count':len(attachments),'delivery':delivery,
                     'publish_date':snap.get('publish_date') if delivery=='scheduled_text' else None,'manual_photo_count':len(snap['images']) if delivery=='scheduled_text' else 0}
            self._step(ident,'wall_post','APPLIED',{'post_id':post_id});self._finish(ident,'APPLIED',receipt)
            try:
                response=self.client.call('wall.getById',token,{'posts':f'-{TARGET}_{post_id}'})
                items=response.get('items',[]) if isinstance(response,dict) else response if isinstance(response,list) else []
                found=next((x for x in items if x.get('id')==post_id and x.get('owner_id')==-TARGET),None)
                if found:
                    attached=[x.get('photo',{}) for x in found.get('attachments',[]) if x.get('type')=='photo']
                    ids=[f"photo{x.get('owner_id')}_{x.get('id')}" for x in attached]
                    schedule_ok=delivery!='scheduled_text' or found.get('date')==snap.get('publish_date')
                    receipt['verification']='VERIFIED' if found.get('text')==snap['message'] and ids==attachments and schedule_ok else 'MISMATCH'
                else:receipt['verification']='UNAVAILABLE'
            except Exception:receipt['verification']='UNAVAILABLE'
            self._finish(ident,'APPLIED',receipt)
        except VkError as e:
            if post_applied:return
            uncertain=e.uncertain or (external_started and stage!='wall_post')
            self._finish(ident,'UNCERTAIN' if uncertain else 'FAILED',{'stage':stage,'error_code':e.code,'verification':'NOT_CHECKED',
                         'message':'Результат неясен. Не повторяйте отправку; проверьте стену/медиа вручную.' if uncertain else 'VK отклонил операцию. Публикация не подтверждена.'})
        except Exception:
            if post_applied:return
            self._finish(ident,'UNCERTAIN' if external_started else 'FAILED',{'stage':stage,'verification':'NOT_CHECKED','message':'Операция прервана. Автоматического повтора нет.'})

    def record_found(self,ident,post_id):
        if type(post_id) is not int or post_id<=0:raise ValueError('Укажите положительный ID найденного поста.')
        if self.get(ident)['state']!='UNCERTAIN':raise ValueError('Ручная сверка доступна только для неясного результата.')
        self._finish(ident,'MANUAL_RECORDED',{'post_id':post_id,'url':f'https://vk.com/wall-{TARGET}_{post_id}','verification':'MANUAL_RECORDED','recorded_at':now(),'actor':'local-operator'})
        return self.get(ident)

    def media_package(self,ident):
        import io,zipfile
        with self.store.connection() as c:row=c.execute('SELECT payload,result,state FROM vk_intents WHERE id=?',(ident,)).fetchone()
        if not row:raise KeyError('Попытка не найдена.')
        snap=json.loads(row['payload']);receipt=json.loads(row['result']);data={'post.txt':snap['message'].encode('utf-8')}
        for i,image in enumerate(snap['images'],1):
            jpeg=square_jpeg((self.store.root/'media'/f"{image['id']}.jpg").read_bytes())
            if hashlib.sha256(jpeg).hexdigest()!=image['sha256']:raise ValueError('Файл фото изменился; пакет не сформирован.')
            data[f'{i:02}.jpg']=jpeg
        details={'target_id':TARGET,'intent_id':ident,'state':row['state'],'post_id':receipt.get('post_id'),'publish_date':snap.get('publish_date'),
                 'files':{name:hashlib.sha256(blob).hexdigest() for name,blob in data.items()}}
        data['manifest.json']=packed(details).encode('utf-8')
        data['README.txt']='Добавьте JPG 01..N к конкретному посту VK вручную до указанного времени. Наличие пакета не означает успешную отправку: смотрите статус и ссылку в Studio.\n'.encode('utf-8')
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            for name,blob in data.items():z.writestr(name,blob)
        return out.getvalue()
