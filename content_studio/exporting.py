from __future__ import annotations

import io
import json
import re
import zipfile
from copy import deepcopy
from html import escape
from pathlib import PurePosixPath

from .content import CHANNELS, digest, image_bytes, render, square_jpeg
from .storage import packed, uid

MAX_ARCHIVE = 100*1024*1024


def document(title, body):
    return '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(title)+'</title><style>body{font:17px/1.65 system-ui;margin:24px;overflow-wrap:anywhere}img{max-width:100%;height:auto}table{border-collapse:collapse;max-width:100%}td,th{border:1px solid #ddd;padding:8px}h1{line-height:1.2}a{color:#126b57}</style><h1>'+escape(title)+'</h1>'+body+'</html>'


def make_export(store,p,kind='transfer'):
    if kind not in ('transfer','backup','final'):raise ValueError('Неизвестный вид экспорта.')
    assets=store.assets(p)
    files={'project.json':packed({'schema_version':1,'project':p}).encode('utf-8')}
    files['asset-map.json']=packed(p['asset_map']).encode()
    files['vk/post.txt']=p['drafts']['vk']['body'].encode('utf-8')
    for n,aid in enumerate(p['drafts']['vk']['media'],1):
        files[f'vk/media/{n:02}.jpg']=square_jpeg(assets[aid]['jpeg'])
    for aid,a in assets.items():
        files[f'media/{aid}.jpg']=a['jpeg']
        if kind=='backup':files[f'originals/{aid}.original']=(store.root/'media'/f'{aid}.original').read_bytes()
    for channel in ('news','article'):
        d=p['drafts'][channel]
        body=render(d['body'],assets,'final' if kind=='final' else 'transfer',p['asset_map'],profile=channel)
        if d['media']:
            for aid in d['media']:
                # Photo order is independent per channel; images already in text are not duplicated.
                if f'data-asset-id="{aid}"' not in d['body']:
                    image=f'<img data-asset-id="{aid}" alt="{escape(assets[aid]["alt"],quote=True)}">'
                    body+=render(image,assets,'final' if kind=='final' else 'transfer',p['asset_map'],profile=channel)
        files[f'site/{channel}/body.html']=body.encode()
        files[f'site/{channel}/preview.html']=document(d['title'] or p['title'],body).encode()
        files[f'site/{channel}/fields.json']=packed({k:d[k] for k in ('title','announcement','seo_title','seo_description','state')}).encode()
    files['README.md']=('STERBRUST Content Studio — локальный пакет\n\n'
        'Экспорт не означает публикацию. VK: post.txt и JPG 01..N в выбранном порядке.\n'
        'Сайт: поля и body.html; preview.html предназначен для просмотра пакета.\n'
        'Сначала вручную загрузите фото штатным способом, затем заполните карту публичных HTTPS URL в Studio и выгрузите HTML для вставки.\n'
        'Без целевого round-trip совместимость конкретной формы Bitrix не подтверждена.\n'
        'backup содержит оригиналы; импорт создаёт новый проект, не публикует ничего.\n').encode()
    manifest={'schema_version':1,'export_kind':kind,'project_id':p['id'],'revision':p['revision'],
              'status':{k:d['state'] for k,d in p['drafts'].items()},'ordered_media':{k:d['media'] for k,d in p['drafts'].items()},
              'files':{name:{'sha256':digest(data),'size':len(data)} for name,data in sorted(files.items())}}
    files['manifest.json']=packed(manifest).encode()
    if sum(len(x) for x in files.values())>MAX_ARCHIVE:raise ValueError('Локальный профиль: пакет не больше 100 MiB. Уменьшите число или размер фото.')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,data)
    return output.getvalue()


def import_project(store,raw):
    if len(raw)>MAX_ARCHIVE:raise ValueError('Архив больше 100 MiB.')
    try:
        z=zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:raise ValueError('Некорректный ZIP.') from None
    with z:
        infos=z.infolist()
        if len(infos)>1000 or sum(i.file_size for i in infos)>MAX_ARCHIVE:raise ValueError('Превышен лимит распаковки (100 MiB / 1000 файлов).')
        names=[i.filename for i in infos]
        if len(names)!=len(set(names)):raise ValueError('Дублирующиеся имена файлов.')
        for i in infos:
            path=PurePosixPath(i.filename)
            if path.is_absolute() or any(x in ('..','.') for x in path.parts) or '\\' in i.filename or ':' in i.filename or (i.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Небезопасный путь в архиве.')
        if not {'manifest.json','project.json'}<=set(names):raise ValueError('Нужен собственный пакет Studio с manifest.json.')
        if z.getinfo('manifest.json').file_size>2*1024*1024 or z.getinfo('project.json').file_size>10*1024*1024:raise ValueError('Слишком большая структура проекта.')
        manifest=json.loads(z.read('manifest.json'));data=json.loads(z.read('project.json'))
        if manifest.get('schema_version')!=1 or data.get('schema_version')!=1:raise ValueError('Версия проекта не поддерживается.')
        if set(manifest.get('files',{})) != set(names)-{'manifest.json'}:raise ValueError('Неполный manifest.')
        for name,meta in manifest['files'].items():
            if digest(z.read(name))!=meta.get('sha256'):raise ValueError('Контрольная сумма файла не совпадает.')
        source=data.get('project',{})
        # Validate in isolated temporary storage first. Failed imports never leave half a user project.
        import tempfile
        from .storage import Store
        with tempfile.TemporaryDirectory(dir=store.root) as tmp:
            staging=Store(tmp);p=staging.create(str(source.get('title','Импорт')))
            remap={}
            for asset in source.get('media',[]):
                aid=asset.get('id','')
                if not re.fullmatch('[0-9a-f]{32}',aid):raise ValueError('Некорректный asset ID.')
                original=f'originals/{aid}.original'
                media=f'media/{aid}.jpg'
                blob=z.read(original if original in names else media)
                jpeg,meta=image_bytes(blob)
                p=staging.media_add(p['id'],blob,jpeg,{**meta,'name':str(asset.get('name','Фото'))[:250]},p['revision']);new=p['media'][-1]['id'];remap[aid]=new
                p['media'][-1].update({k:str(asset.get(k,'')) for k in ('alt','origin','rights')})
            candidate=deepcopy(source);candidate['media']=p['media']
            candidate['asset_map']={remap[k]:v for k,v in source.get('asset_map',{}).items() if k in remap}
            for ch in CHANNELS:
                d=candidate['drafts'][ch];d['state']='DRAFT';d['media']=[remap[x] for x in d['media']]
                for old,new in remap.items():d['body']=d['body'].replace(old,new)
            candidate['accept_cleaning']=True
            p=staging.save(p['id'],candidate,p['revision'])
            # All bytes and content have been validated. Commit the import in one destination transaction.
            new_id=uid();p['id']=new_id;p['revision']=0
            import os
            for asset in p['media']:
                for ext in ('jpg','original'):
                    dest=store.root/'media'/f"{asset['id']}.{ext}"
                    with dest.open('xb') as f:f.write((staging.root/'media'/dest.name).read_bytes());f.flush();os.fsync(f.fileno())
            from .storage import now
            with store.connection() as c:
                c.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(new_id,p['title'],0,packed(p),now()))
                for asset in p['media']:c.execute('INSERT INTO media VALUES(?,?,?)',(asset['id'],new_id,packed(asset)))
                store._revision(c,p)
                store._created(c,p)
            return p
