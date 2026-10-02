"""Read saved Beka29 scope only; no network or database access."""
from pathlib import Path
import sys,json,hashlib,re
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.resolve_reviews_offline import load,save,sha
PRIOR=ROOT/'reports/RC_LOCAL/BEKA_GAP_VERIFIED_2026-10-01'
OUT=ROOT/'reports/RC_LOCAL/BEKA_VISIBLE_IDENTITY_2026-10-01'

def inputs():
    ledger=load(PRIOR/'LEDGER.json'); rows=[{**r,'origin':'HUMAN'} for r in ledger['records'] if r['status']=='HUMAN_REVIEW_REQUIRED']
    wanted={r['evidence_ref'].removeprefix('sha256:') for r in rows}; found={}
    for folder in ledger['detail_directories']:
        for f in (ROOT.parents[1]/folder).rglob('*.html'):
            digest=hashlib.sha256(f.read_text(encoding='utf-8').encode()).hexdigest()
            if digest in wanted: found[digest]=f
    for r in rows:
        r['capture_path']=str(found[r['evidence_ref'].removeprefix('sha256:')])
    for r in load(PRIOR/'RUN.json')['http_rows']:
        if r['status']!='REVIEW': continue
        files=list((PRIOR/'evidence'/r['source']).rglob(r['evidence_ref'].split('/')[-1]))
        if len(files)!=1: raise ValueError('HTTP evidence path not unique')
        rows.append({**r,'origin':'HTTP_REVIEW','capture_path':str(files[0])})
    if len(rows)!=29 or len({(r['source'],r['url']) for r in rows})!=29: raise ValueError('29 scope mismatch')
    return rows

if __name__=='__main__':
    from universal_supplier.beka_visible_identity import assess,STALE_URL
    rows=inputs(); scope={r['url'] for r in rows}; audits=[]
    for r in rows:
        f=Path(r['capture_path'])
        a=assess(r['source'],r['url'],f.read_text(encoding='utf-8'),approved_urls=scope,operator_stale=r['url']==STALE_URL)
        a.update(origin=r['origin'],capture_path=str(f),capture_sha256=sha(f))
        audits.append(a)
        print(json.dumps({k:a[k] for k in ('url','origin','verdict','visible_primary_model','visible_article','reasons')},ensure_ascii=True))
    OUT.mkdir(exist_ok=True); save(OUT/'SOURCE_IDENTITY_AUDIT.json',audits)
