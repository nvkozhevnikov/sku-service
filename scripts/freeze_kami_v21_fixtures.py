"""Mechanical freeze of verified35 calibration cases, not a matcher rerun."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    folder=ROOT/'reports/KAMI_MATCHING_POLICY_V2_2026-10-08'
    proposal=json.loads((folder/'V2_1_PROPOSAL_DATA.json').read_bytes())
    inputs=json.loads((folder/'V2_1_QA_INPUTS.json').read_bytes())
    fixtures=proposal['fixtures'];assert len(fixtures)==35 and all(f['status']=='PASS' for f in fixtures)
    needed={pid for f in fixtures for pid in f['audit']['retrieval_ids']}
    payload={'provenance':'OPERATOR-CONFIRMED 2026-10-08; verified frozen actual-source calibration subset, not live API evidence',
        'input_hashes':proposal['summary']['input_hashes'],
        'proposal_sha256':hashlib.sha256((folder/'V2_1_PROPOSAL_DATA.json').read_bytes()).hexdigest(),
        'cases':[{'external_id':f['external_id'],'expected_id':f['expected_id'],'retrieval_ids':f['audit']['retrieval_ids'],
            'source':inputs['sources'][f['external_id']]} for f in fixtures],
        'targets':{pid:inputs['targets'][pid] for pid in sorted(needed)}}
    target=ROOT/'tests/fixtures/kami_v21_operator_calibrations.json'
    if target.exists():raise RuntimeError('Fixture already exists: review rather than overwrite')
    raw=json.dumps(payload,ensure_ascii=False,indent=2).encode('utf8')
    assert len(raw)<2_000_000,'Unexpected bulk fixture'
    target.parent.mkdir(exist_ok=True);target.write_bytes(raw)
    print(json.dumps({'cases':35,'targets':len(needed),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}))


if __name__=='__main__':main()
