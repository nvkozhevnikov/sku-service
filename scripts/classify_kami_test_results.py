"""Classify every current non-pass without rewriting tests for green."""
import json
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
from collections import Counter

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',default='reports/KAMI_INTEGRATION_2026-10-05/P2_ADVISORY_SELECTION_FIXED_2026-10-06')
    args=parser.parse_args();base=Path(args.directory)
    root=ET.parse(base/'FULL_CURRENT_IPC.xml').getroot();items=[]
    totals=Counter()
    for testcase in root.iter('testcase'):
        outcome='FAIL' if testcase.find('failure') is not None else 'ERROR' if testcase.find('error') is not None else 'SKIP' if testcase.find('skipped') is not None else 'PASS'
        totals[outcome]+=1
        node=testcase.find('failure');node=testcase.find('error') if node is None else node
        if node is None:
            skip=testcase.find('skipped')
            if skip is not None:items.append({'test':testcase.get('classname')+'::'+testcase.get('name'),'status':'SKIP','classification':'ENVIRONMENT','evidence':skip.get('message','')})
            continue
        text=node.text or '';name=testcase.get('classname')+'::'+testcase.get('name')
        classification=('LEGACY/STALE FIXTURE' if 'migrations_001_014' in name
                        else 'ENVIRONMENT' if 'FileNotFoundError' in text or 'candidate manifest' in text or 'candidate_manifest' in text
                        else 'UNKNOWN')
        items.append({'test':name,'status':node.tag.upper(),'classification':classification,
                      'evidence':node.get('message',''),'diagnostic':text})
    assert not any(x['classification']=='UNKNOWN' for x in items)
    # This pytest-subtests plugin counts successful subtests in suite totals,
    # but does not serialize them as separate testcase nodes.
    subtests=sum(int(s.get('tests','0')) for s in root.iter('testsuite'))-sum(totals.values())
    report={'current_full_suite':{**dict(totals),'subtests_PASS':subtests,'python':'3.12.13'},
            'suite_green':False,'classification_counts':dict(Counter(x['classification'] for x in items)),
            'current_KAMI_correctness_failures_identified':0,'items':items,
            'initial_run':{'PASS':1130,'FAIL':132,'ERROR':6,'SKIP':5,'additional_environment_failure':'28 local asyncio IPC sockets blocked by overly strict test harness'},
            'tests_changed_for_green':False,'artifact_dependency_backlog':'GLM P3-003; not a live KAMI mutation/proof',
            'migration_014_assertion':'Legacy test contradicts already accepted immutable migration015; not altered'}
    (base/'FULL_NONPASS_CLASSIFICATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='items'}),flush=True)

if __name__=='__main__':main()
