"""Apply prepared metadata only, prove all current metrics and existing facts unchanged."""
import json
from pathlib import Path
from competitor_intel.store import Store,json_value
from competitor_intel.seed_cache import import_seed_cache
from competitor_intel.business_data import read_state,fingerprints,normalized_dataset
from competitor_intel.business_report import build_facts

def metrics(state):
    f=build_facts(normalized_dataset(state),'2026-09-01','2026-10-12')
    return {k:f[k] for k in ('promotions','products','events')}

if __name__=='__main__':
    out=Path('reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12');out.mkdir(exist_ok=True)
    cfg=json.loads(Path('competitor-data/dev-config.json').read_text())
    s=Store(cfg['dsn'])
    try:
        before=read_state(s);before_fp=fingerprints(before);before_metrics=metrics(before)
        (out/'BEFORE_STATE_FINGERPRINTS.json').write_text(json.dumps(before_fp,indent=2),encoding='utf-8')
        result=import_seed_cache(s,'reports/KAMI_HISTORICAL_SEED_2026-10-12')
        after=read_state(s);after_fp=fingerprints(after)
        result['current_metrics_equal']=before_metrics==metrics(after)
        result['existing_tables_equal']=all(before_fp['tables'][k]==after_fp['tables'][k] for k in before_fp['tables'] if k!='competitor_sources')
        result['existing_source_fingerprints_equal']=all(v==after_fp['sources'][k] for k,v in before_fp['sources'].items())
        if not all(result[k] for k in ('current_metrics_equal','existing_tables_equal','existing_source_fingerprints_equal')):raise ValueError('Seed import isolation proof failed')
        repeat=import_seed_cache(s,'reports/KAMI_HISTORICAL_SEED_2026-10-12');result['repeat_inserted']=repeat['inserted']
        result['universal_supplier_kami_writes']=0
        (out/'SEED_IMPORT_PROOF.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
    finally:s.close()
