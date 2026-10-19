"""One bounded read-only search, no source connections or restore."""
from pathlib import Path
import os,re,json,time,hashlib,zipfile

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parents[1]/'reports/integration/MISSING_SOURCE_SEARCH.json'
SKIP={'.git','.venv','.venv-audit','node_modules','__pycache__','.pytest_cache','tools','pg17','pgdata','base','global','pg_wal'}
PAT=re.compile(r'vekprom|intervesp|55452',re.I)
META=re.compile(r'backup|dump|manifest|checkpoint|final.status|final.report',re.I)

def main():
    start=time.monotonic();found=[];archives=[];errors=[];directories=0
    roots=[ROOT/'work',ROOT/'reports',ROOT/'tmp',ROOT/'archives',ROOT/'archive',Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier']
    for root in roots:
        if not root.exists():continue
        for current,dirs,files in os.walk(root,onerror=lambda e:errors.append(str(getattr(e,'filename','')))):
            directories+=1;p=Path(current)
            dirs[:]=[d for d in dirs if d.lower() not in SKIP and not d.startswith(('pytest','test-temp','tmp'))]
            if len(p.relative_to(root).parts)>12:dirs[:]=[]
            if directories>35000 or time.monotonic()-start>90:raise RuntimeError('Bound exceeded; no complete-search claim')
            for name in files:
                q=p/name;suffix=q.suffix.lower()
                if suffix in ('.dump','.backup','.bak','.zip','.7z','.gz','.tar'):
                    record={'path':str(q),'bytes':q.stat().st_size,'source_hint':bool(PAT.search(str(q)))}
                    if suffix=='.zip':archives.append(record)
                    else:found.append(record)
                elif suffix in ('.json','.md','.txt') and META.search(name) and PAT.search(str(p)):
                    if q.stat().st_size>200000:continue
                    body=q.read_text(encoding='utf-8-sig',errors='replace')
                    # Record matching file identity only, never credentials/body.
                    if PAT.search(body):found.append({'path':str(q),'bytes':q.stat().st_size,'kind':'manifest_or_checkpoint','sha256':hashlib.sha256(q.read_bytes()).hexdigest()})
    archive_hits=[]
    for rec in archives:
        if not (rec['source_hint'] or META.search(Path(rec['path']).name)):continue
        try:
            with zipfile.ZipFile(rec['path']) as z:
                hits=[n for n in z.namelist() if n.lower().endswith(('.dump','.backup')) or (PAT.search(n) and META.search(n))]
            if hits:archive_hits.append({**rec,'entries':hits})
        except (OSError,zipfile.BadZipFile):errors.append(rec['path'])
    result={'roots':[str(p) for p in roots],'directories_scanned':directories,'elapsed_seconds':round(time.monotonic()-start,2),'files':found,'archive_hits':archive_hits,'inaccessible_paths':errors,'status':'SEARCH_COMPLETE_WITH_DECLARED_ACCESS_GAPS','restores':0,'source_db_starts':0}
    OUT.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
