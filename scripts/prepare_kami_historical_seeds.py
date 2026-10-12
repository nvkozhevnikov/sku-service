import argparse
import json
from pathlib import Path
from competitor_intel.historical_seed import export_corpus,plan_refresh

p=argparse.ArgumentParser(description='Read-only saved KAMI metadata → HISTORICAL_SEED; no LIVE crawl/DB write')
p.add_argument('--corpus',type=Path,required=True);p.add_argument('--expected-sha',required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args(); result=export_corpus(a.corpus,a.output,a.expected_sha)
seeds=[json.loads(line) for line in (a.output/'HISTORICAL_SEEDS.jsonl').read_text(encoding='utf-8').splitlines()]
(a.output/'REFRESH_PLAN.json').write_text(json.dumps(plan_refresh(seeds),ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
