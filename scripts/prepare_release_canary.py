"""Prepare/inspect proposal only. Does not enqueue, fetch, migrate or apply matching."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from universal_supplier.control_plane.release_canary import prepare,validate

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('prepare','inspect'))
    p.add_argument('--file',required=True,type=Path)
    args=p.parse_args()
    if args.action=='prepare':
        if args.file.exists():raise FileExistsError('CANARY_PROPOSAL_ALREADY_EXISTS')
        from universal_supplier.control_plane.runtime_config import runtime_config
        value=prepare(runtime_config())
        with args.file.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    else:value=validate(json.loads(args.file.read_text(encoding='utf-8')))
    print(json.dumps({'file':str(args.file),'sha256':hashlib.sha256(args.file.read_bytes()).hexdigest(),
        'head':value['head'],'products':value['product_url_count'],'execution_authorized':False,
        'HTTP':0,'enqueue':0,'matching_apply':0}))

if __name__=='__main__':main()
