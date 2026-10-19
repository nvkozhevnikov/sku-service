"""Preserve pinned fixture bytes and bounded helper import closure."""
import ast
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
def blob(ref,path):return subprocess.check_output(['git','-C',str(ROOT),'show',ref+':'+path])
def helpers(ref,seeds):
    pending=list(seeds);seen=set()
    while pending:
        path=pending.pop()
        if path in seen:continue
        seen.add(path)
        data=blob(ref,path)
        dest=ROOT/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        tree=ast.parse(data.decode('utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom) and node.module:
                if node.module=='scripts':pending += ['scripts/'+x.name+'.py' for x in node.names]
                elif node.module.startswith('scripts.'):
                    pending.append(node.module.replace('.','/')+'.py')
    return seen

if __name__=='__main__':
    # Original -text attributes keep sealed saved HTML exact under autocrlf.
    attrs=ROOT/'.gitattributes'
    existing=attrs.read_bytes()
    source=blob('94deab99','.gitattributes')
    attrs.write_bytes(existing+b'\n# Frozen source bytes from LTT/USS checkpoint\n'+source)
    tracked=subprocess.check_output(['git','-C',str(ROOT),'ls-files','tests/fixtures']).decode().splitlines()
    for path in tracked:
        if any(x in path for x in ('/ltt_uss/','/ltt_uss_units/','/uss_semantic_tables/','/uss_terminal_batch/')):
            (ROOT/path).write_bytes(blob('94deab99',path))
    seen=helpers('94deab99',['scripts/uss_terminal_batch_fix.py','scripts/correct_uss_own_heading.py','scripts/run_ltt_uss_live_canary.py'])
    seen |= helpers('c919870',['scripts/qa_competitor_worker_backend.py','scripts/qa_competitor_worker_ui.py','scripts/run_competitor_monitoring_ui.py'])
    print('Bounded QA helper dependency closure:',len(seen))
