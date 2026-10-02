"""Read-only code/branch inclusion inventory and forbidden-file gate."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]

def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,encoding='utf-8').strip()

def audit(output, staged=False):
    paths=git('diff','--cached','--name-only').splitlines() if staged else git('ls-files','--cached','--others','--exclude-standard').splitlines()
    forbidden=[]; secrets=[]; heavy=[]
    for name in paths:
        p=ROOT/name
        if not p.is_file():continue
        if name.startswith(('reports/','release/database/','server-data/','work/','.venv/')) or p.suffix in {'.dump','.backup','.partial'} or (p.name.startswith('.env') and p.name not in {'.env.example','.env.server.example'}):
            forbidden.append(name)
        if p.stat().st_size>2_000_000:heavy.append(name)
        if p.stat().st_size<2_000_000:
            body=p.read_text(encoding='utf-8',errors='replace')
            # Full authenticated webhook URLs/private-key PEM are never code.
            if re.search(r'https?://[^ /]+/rest/[0-9]+/[A-Za-z0-9]{10,}/|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----',body):
                secrets.append(name)
    branches=[]
    for line in git('for-each-ref','--format=%(refname:short) %(objectname)','refs/heads/feature/').splitlines():
        branch,head=line.split(' ',1)
        names=git('diff','--name-only','6df49f3',head).splitlines()
        if branch.endswith(('fresh-catalog-refresh-rc1','full-discovery-rc1','new-readiness-offline-two-rc1','priority-near-complete-rc1')):
            disposition='accepted data/results already verified in frozen proposals or RC DB; raw evidence stays external; no branch merge'
        elif 'esol' in branch:
            disposition='not needed for neutral RC; blocked importer scope; preserved branch, not integrated'
        elif branch.endswith(('ci-rc1','reviewpack-rc1','matching-audit-rc1','consolidation-audit-rc1')):
            disposition='independent optional audit/CI/review-package tooling; no blind merge; current focused core checks and server config included directly'
        else:disposition='base core ancestry plus reviewed current working-tree implementation'
        branches.append({'branch':branch,'head':head,'changed_paths':names,'disposition':disposition})
    result={'tracked_candidate_files':len(paths),'forbidden_paths':forbidden,'secret_paths':secrets,'heavy_paths':heavy,
            'base_commit':git('rev-parse','6df49f3'),'production_before':git('rev-parse','production'),
            'remote_production_before':git('rev-parse','origin/production'),'branches':branches,
            'scope':'code integration, no blind merges; feature branches retained'}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('tracked_candidate_files','forbidden_paths','secret_paths','heavy_paths')}))
    if forbidden or secrets or heavy:raise SystemExit('CODE_HYGIENE_FAILED')
if __name__=='__main__':audit(Path(sys.argv[1]),'--staged' in sys.argv)
