"""Independent one-shot entrypoint; same CollectionService and execution use-case."""
import argparse,json
from pathlib import Path
from .execution import CollectionService
from .worker_dispatch import load,update_dispatch

def main():
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);args=p.parse_args()
    request=load(args.request);runtime=Path(request['runtime'])
    if args.request.resolve()!=runtime/'requests'/f"{request['request_id']}.json":raise ValueError('Invalid request identity')
    service=CollectionService(request['dev_config'],request['schedule_config'],runtime,fixture_spec=request.get('fixture_spec'))
    try:
        result=service.run(request['origin'],request['selected_sources'],request['max_pages'],request_id=request['request_id'])
        update_dispatch(runtime,request['request_id'],result);return result['exit_code']
    except Exception as error:
        update_dispatch(runtime,request['request_id'],{'status':'FAILED','errors':[{'phase':'worker_bootstrap','reason':type(error).__name__}]});return 2

if __name__=='__main__':raise SystemExit(main())
