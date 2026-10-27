"""Local bounded execution reports; a recent heartbeat is not proof of successful work."""
from datetime import datetime,timezone
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[2]/'reports/automation-runtime'

def overview(root=None):
    root=Path(root or ROOT);result=[]
    if not root.exists():return result
    for folder in sorted(root.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
        if not folder.is_dir() or folder.is_symlink():continue
        try:
            session=json.loads((folder/'SESSION.json').read_text(encoding='utf-8'))
            state={'id':folder.name,'session':session,'worker':'UNKNOWN','latest':None,'final':None}
            for filename,key in [('heartbeat.json','heartbeat'),('hourly/latest.json','latest'),('FINAL.json','final'),('FINAL_UNVERIFIED.json','unverified')]:
                path=folder/filename
                if path.is_file():state[key]=json.loads(path.read_text(encoding='utf-8'))
            if state.get('unverified'):state['worker']='STOPPED_UNVERIFIED'
            elif state['final']:state['worker']='STOPPED'
            elif state.get('heartbeat'):
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(state['heartbeat']['at'])).total_seconds()
                state['worker']='RECENT_HEARTBEAT' if 0<=age<=30 else 'STALE_HEARTBEAT'
            result.append(state)
        except (OSError,ValueError,KeyError):result.append({'id':folder.name,'worker':'REPORT_UNREADABLE'})
    return result

def stop(identifier,root=None):
    root=Path(root or ROOT).resolve();folder=(root/identifier).resolve()
    if not folder.is_relative_to(root) or folder==root or not (folder/'SESSION.json').is_file():
        raise ValueError('AUTOMATION_SESSION_NOT_FOUND')
    from .source_hourly import atomic_text
    atomic_text(folder/'STOP_REQUESTED','OPERATOR_STOP')
