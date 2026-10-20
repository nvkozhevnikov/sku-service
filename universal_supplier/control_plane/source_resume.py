"""Code-only checkpoint/resume primitives in the existing evidence tree.

No HTTP, SQL, scheduler or UI action is called. Source collectors still must be
wired and reviewed individually; a prepared frontier is not verified discovery.
"""
from pathlib import Path
from datetime import datetime
from universal_supplier.commercial_persistence import HttpCapture
from copy import deepcopy
from contextlib import contextmanager
import hashlib,json,os,threading
from universal_supplier.kami_checkpoint import write_checkpoint
from universal_supplier.postgres import SUPPLIER_CONFIGS

STATES=frozenset({'COMMITTED','HTTP_CAPTURED_NOT_COMMITTED','FAILED','NOT_REQUESTED','UNCERTAIN'})

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def original_capture_present(receipt,entry):
    value=receipt.get('original_capture_metadata')
    if not isinstance(value,dict):return False
    try:
        fields=dict(value);fields['observed_at']=datetime.fromisoformat(fields['observed_at'])
        capture=HttpCapture(**fields)
        return capture.requested_url==entry['url'] and capture.fingerprint==receipt.get('capture_fingerprint')
    except (ValueError,TypeError,KeyError):return False

def classify(entries,receipts,sql_probe,*,source,parent_job_id,manifest_sha256):
    """Probe is supplied by an approved reader and returns COMMITTED/NOT_FOUND/UNKNOWN.

    A missing receipt/unknown SQL result is never proof HTTP was not requested.
    The probe must bind the exact job/capture, not merely find an older product.
    """
    if source not in SUPPLIER_CONFIGS or type(parent_job_id) is not int or parent_job_id<=0:raise ValueError('RESUME_OWNER')
    if len(manifest_sha256)!=64 or any(c not in '0123456789abcdef' for c in manifest_sha256):raise ValueError('MANIFEST_SHA256')
    origin={'source':source,'job_id':parent_job_id,'manifest_sha256':manifest_sha256}
    for receipt in receipts:
        if receipt.get('origin')!=origin:raise ValueError('RECEIPT_OWNER_MISMATCH')
    by_url={r['url']:r for r in receipts}
    if len(by_url)!=len(receipts):raise ValueError('DUPLICATE_RECEIPT_URL')
    if set(by_url)-{e['url'] for e in entries}:raise ValueError('RECEIPT_URL_OUTSIDE_MANIFEST')
    output=[]
    for entry in entries:
        receipt=by_url.get(entry['url']);sql=sql_probe(entry,receipt)
        if sql=='COMMITTED':state='COMMITTED'
        elif sql!='NOT_FOUND' or receipt is None:state='UNCERTAIN'
        else:
            state=receipt.get('state','UNCERTAIN')
            if state not in STATES:raise ValueError('UNKNOWN_RECEIPT_STATE')
            if state=='COMMITTED':state='UNCERTAIN'
            if state=='HTTP_CAPTURED_NOT_COMMITTED' and not original_capture_present(receipt,entry):state='UNCERTAIN'
        output.append({'entry':deepcopy(entry),'receipt':deepcopy(receipt),'state':state,'origin':dict(origin)})
    return output

def pending_manifest(original,classified,*,parent_job_id,parent_manifest_sha256,retry_failed=False):
    """New pending-only manifest data; original snapshot remains byte-for-byte owned by caller."""
    expected={'source':original['supplier_code'],'job_id':parent_job_id,'manifest_sha256':parent_manifest_sha256}
    if any(r.get('origin')!=expected for r in classified):raise ValueError('CLASSIFICATION_OWNER_MISMATCH')
    known={e['url']:e for e in original['entries']}
    if len(known)!=len(original['entries']):raise ValueError('DUPLICATE_MANIFEST_URL')
    if len(classified)!=len(known) or {x['entry']['url'] for x in classified}!=set(known):raise ValueError('INCOMPLETE_OR_DUPLICATE_CLASSIFICATION')
    http=[];offline=[];uncertain=[];failed=[]
    for row in classified:
        entry=row['entry'];state=row['state']
        if entry!=known[entry['url']]:raise ValueError('MANIFEST_ENTRY_CHANGED')
        if state=='NOT_REQUESTED' or (state=='FAILED' and retry_failed):http.append(deepcopy(entry))
        elif state=='HTTP_CAPTURED_NOT_COMMITTED':offline.append(deepcopy(row))
        elif state=='UNCERTAIN':uncertain.append(entry['url'])
        elif state=='FAILED':failed.append(entry['url'])
    return {'http_pending':{**deepcopy(original),'entries':http,'resume':{'parent_job_id':parent_job_id,'parent_manifest_sha256':parent_manifest_sha256,'mode':'PENDING_ONLY','discovery_complete':False}},
            'offline_pending':offline,'uncertain':uncertain,'failed_requires_decision':failed,'automatic_resume_allowed':not uncertain and not failed}

class SourceCheckpoint:
    """Single leased collector owns this job checkpoint; O_EXCL guards concurrent writers.

    A crashed writer's .writer.lock is not automatically deleted. Operator must
    verify the old owner and lease before clearing it. Atomic writer is KAMI's.
    """
    def __init__(self,evidence_root,source,job_id,manifest_sha,*,validator,lease_acquired=False):
        if source not in SUPPLIER_CONFIGS or type(job_id) is not int or job_id<=0:raise ValueError('CHECKPOINT_OWNER')
        if not lease_acquired:raise PermissionError('COLLECTOR_LEASE_REQUIRED')
        if len(manifest_sha)!=64 or any(c not in '0123456789abcdef' for c in manifest_sha):raise ValueError('MANIFEST_SHA256')
        self.path=Path(evidence_root).resolve()/source/('job-'+str(job_id))/'checkpoint.json';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.validator=validator;self.lock=threading.RLock();self.expected=None
        self.state={'format':'source-discovery-checkpoint/v1','source':source,'job_id':job_id,'manifest_sha256':manifest_sha,'revision':0,'frontier':[],'inflight':{},'terminal':{},'visited':[],'discovery_receipts':{},'discovery_finished':False,'category_failures':[]}
        if self.path.exists():
            existing=json.loads(self.path.read_text(encoding='utf-8'));checksum=existing.pop('checksum')
            if digest(existing)!=checksum:raise ValueError('CHECKPOINT_CHECKSUM')
            for key in ('format','source','job_id','manifest_sha256'):
                if existing[key]!=self.state[key]:raise ValueError('CHECKPOINT_OWNER_MISMATCH')
            for url in list(existing['frontier'])+list(existing['inflight'])+list(existing['terminal'])+list(existing['visited']):self.validator(url)
            self.state=existing;self.expected=checksum
    def _save(self):
        guard=self.path.with_suffix('.writer.lock');fd=os.open(guard,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        try:
            if self.path.exists():
                recorded=json.loads(self.path.read_text(encoding='utf-8'))
                recorded_checksum=recorded.pop('checksum')
                if digest(recorded)!=recorded_checksum:raise ValueError('CHECKPOINT_CHECKSUM')
                if recorded_checksum!=self.expected:raise RuntimeError('CHECKPOINT_CHANGED_BY_ANOTHER_WRITER')
            self.state['revision']+=1;checksum=digest(self.state)
            write_checkpoint(self.path,{**self.state,'checksum':checksum},durable=True);self.expected=checksum
        finally:os.close(fd);guard.unlink()
    @contextmanager
    def _change(self):
        with self.lock:
            old=deepcopy(self.state);expected=self.expected
            try:
                yield
                self._save()
            except BaseException:
                self.state=old;self.expected=expected;raise
    def add_urls(self,urls):
        with self._change():
            known=set(self.state['frontier'])|set(self.state['inflight'])|set(self.state['terminal'])|set(self.state['visited'])
            for url in urls:
                self.validator(url)
                if url not in known:self.state['frontier'].append(url);known.add(url)
    def claim_batch(self,limit):
        if type(limit) is not int or not 1<=limit<=1000:raise ValueError('BATCH_LIMIT')
        with self._change():
            batch=self.state['frontier'][:limit];self.state['frontier']=self.state['frontier'][limit:]
            for url in batch:self.state['inflight'][url]={'state':'UNCERTAIN'}
            return batch
    def record_terminal(self,receipts):
        with self._change():
            for receipt in receipts:
                url=receipt['url'];state=receipt['state'];self.validator(url)
                if state not in STATES:raise ValueError('UNKNOWN_RECEIPT_STATE')
                if url not in self.state['inflight'] and url not in self.state['terminal']:raise ValueError('URL_NOT_CLAIMED')
                if state=='COMMITTED' and not receipt.get('sql_verified'):raise ValueError('COMMIT_PROOF_REQUIRED')
                previous=self.state['terminal'].get(url,{'history':[]})
                if previous.get('state')=='COMMITTED' and state!='COMMITTED':raise ValueError('COMMITTED_CANNOT_BE_DOWNGRADED')
                history=previous['history']
                if not history or history[-1]!=receipt:history=history+[deepcopy(receipt)]
                self.state['terminal'][url]={'state':state,'receipt':deepcopy(receipt),'history':history};self.state['inflight'].pop(url,None)
    def complete_discovery_node(self,url,children,*,evidence_sha256):
        children=list(children)
        if len(evidence_sha256)!=64 or any(c not in '0123456789abcdef' for c in evidence_sha256):raise ValueError('DISCOVERY_EVIDENCE_SHA256')
        with self._change():
            if url not in self.state['inflight']:raise ValueError('URL_NOT_CLAIMED')
            self.validator(url)
            known=set(self.state['frontier'])|set(self.state['inflight'])|set(self.state['terminal'])|set(self.state['visited'])
            for child in children:
                self.validator(child)
                if child not in known:self.state['frontier'].append(child);known.add(child)
            self.state['inflight'].pop(url);self.state['visited'].append(url)
            self.state['discovery_receipts'][url]={'evidence_sha256':evidence_sha256,'children':list(children)}
    def finish_discovery(self,*,category_failures=()):
        with self._change():
            self.state['discovery_finished']=True;self.state['category_failures']=list(category_failures)
    @property
    def complete(self):
        return bool(self.state['discovery_finished'] and not self.state['category_failures'] and not self.state['frontier'] and not self.state['inflight'] and all(r['state']=='COMMITTED' for r in self.state['terminal'].values()))
