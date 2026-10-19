"""Isolated CLI adapter for process acceptance; invokes the shared service only."""
import argparse,json
from pathlib import Path
from competitor_intel.execution import CollectionService

def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True);p.add_argument('--origin',choices=['CLI_MANUAL','SCHEDULED'],required=True);a=p.parse_args()
    service=CollectionService(None,a.runtime/'schedule.json',a.runtime,fixture_spec=a.runtime/'fixture.json')
    result=service.run(a.origin,['metalmaster'],1);print(json.dumps(result));return result['exit_code']

if __name__=='__main__':raise SystemExit(main())
