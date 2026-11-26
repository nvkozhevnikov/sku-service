"""Code-only checkpoint/resume primitives in the existing evidence tree.

No HTTP, SQL, scheduler or UI action is called here. The nine wrapper collectors
use these primitives through source_runtime; offline replay is not live discovery.
"""
from pathlib import Path
from datetime import datetime
from universal_supplier.commercial_persistence import HttpCapture
from copy import deepcopy
from contextlib import contextmanager
import hashlib,json,os,re,threading
from universal_supplier.kami_checkpoint import write_checkpoint
from universal_supplier.postgres import SUPPLIER_CONFIGS
from .transport_diagnostics import MANUAL_DISCONNECT, MANUAL_TIMEOUT, manual_transport_category, safe_message

STATES=frozenset({'COMMITTED','HTTP_CAPTURED_NOT_COMMITTED','FAILED','NOT_REQUESTED','UNCERTAIN','QUARANTINED'})
MANUAL_TRANSPORT_ERRORS = frozenset({MANUAL_TIMEOUT, MANUAL_DISCONNECT})
MANUAL_REDIRECT = 'manual_out_of_scope_redirect'
MANUAL_STOP_INTERRUPTED = 'manual_stop_interrupted'
STOPPED_REQUEST = 'RequestAborted:HOST_STOPPED'
FROZEN_BATCH_REFUSAL = 'CANARY_URL_OUTSIDE_FROZEN_BATCH'


def manual_transport_receipt(origin, url, job_id, evidence_path, *, expected_error=None):
    path=Path(evidence_path)
    raw=path.read_bytes()
    exception=json.loads(raw)['diagnostics']['original_exception']
    category=manual_transport_category(exception)
    if category is None or (expected_error is not None
            and expected_error!=exception['type']+':'+exception['message']):
        raise ValueError('DEFERRED_TRANSPORT_PROOF_REQUIRED')
    proof={'job_id':job_id,'file':path.name,'sha256':hashlib.sha256(raw).hexdigest()}
    reason={MANUAL_TIMEOUT:'READ_TIMEOUT_UNKNOWN',MANUAL_DISCONNECT:'REMOTE_DISCONNECTED_UNKNOWN'}[category]
    receipt={'url':url,'state':'QUARANTINED','origin':dict(origin),'timeout_evidence':proof,
             'isolation':{'before_ingest':True,'category':category,'reason':reason}}
    if not quarantine_evidence_present(receipt,{'url':url}):raise ValueError('DEFERRED_TIMEOUT_PROOF_REQUIRED')
    return receipt


def manual_redirect_receipt(origin,url,job_id,evidence_path,*,legacy=False):
    path=Path(evidence_path)
    receipt={'url':url,'state':'QUARANTINED','origin':dict(origin),
             'redirect_evidence':{'job_id':job_id,'file':path.name,
                                  'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'legacy':legacy},
             'isolation':{'category':MANUAL_REDIRECT,'before_ingest':True,
                          'reason':FROZEN_BATCH_REFUSAL if legacy else 'PRODUCT_REDIRECT_OUTSIDE_BATCH'}}
    if not quarantine_evidence_present(receipt,{'url':url}):
        raise ValueError('MANUAL_REDIRECT_PROOF_REQUIRED')
    return receipt


def manual_stop_receipt(origin, url, job_id, path):
    """Isolate an interrupted GET. Its transport outcome remains unknown."""
    proof = {'job_id':job_id,'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    receipt = {'url':url,'state':'QUARANTINED','origin':dict(origin),'stop_evidence':proof,
               'isolation':{'category':MANUAL_STOP_INTERRUPTED,'before_ingest':True,'reason':STOPPED_REQUEST}}
    if not quarantine_evidence_present(receipt, {'url':url}):
        raise ValueError('MANUAL_STOP_PROOF_REQUIRED')
    return receipt


def _manual_stop_proven(receipt, entry):
    from .source_runtime import job_dir
    origin = receipt['origin']
    proof = receipt['stop_evidence']
    source, job_id, name = origin['source'], proof['job_id'], proof['file']
    if (source not in SUPPLIER_CONFIGS or type(job_id) is not int or job_id < origin['job_id']
            or not re.fullmatch(r'[a-f0-9]{64}\.json', name)):
        return False
    folder = job_dir(source, job_id)
    raw = (folder/'transport-errors'/name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != proof['sha256']:
        return False
    record = json.loads(raw)
    stop = json.loads((folder/'FIRST_STOP.json').read_bytes())
    diagnostic = record['diagnostics']
    exception = diagnostic['original_exception']
    return (
        receipt['url'] == entry['url'] and record['url'] == safe_message(entry['url'])
        and record['job_id'] == job_id and record['owner_job_id'] == origin['job_id']
        and record['input_sha256'] == origin['manifest_sha256'] and record['http_status'] is None
        and record['transport_started'] is True and stop['job_id'] == job_id and stop['reason'] == 'OPERATOR_STOP'
        and diagnostic['source'] == source and diagnostic['request_scope'] == 'product'
        and diagnostic['wire_outcome'] == 'UNKNOWN' and diagnostic['response_available'] is False
        and (exception['module'], exception['type'], exception['message']) ==
            ('universal_supplier.control_plane.rate_control', 'RequestAborted', 'HOST_STOPPED'))


def _manual_redirect_proven(receipt,entry):
    from .source_runtime import job_dir
    proof=receipt['redirect_evidence'];origin=receipt['origin'];jid=proof['job_id'];source=origin['source']
    name=proof['file'];url=entry['url'];legacy=proof['legacy']
    if (source not in SUPPLIER_CONFIGS or type(jid) is not int or jid<origin['job_id']
            or type(legacy) is not bool or not re.fullmatch(r'[a-f0-9]{64}\.json',name)):
        return False
    folder=job_dir(source,jid)
    raw=(folder/'transport-errors'/name).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=proof['sha256']:return False
    record=json.loads(raw);diag=record['diagnostics'];error=diag['original_exception']
    if not (receipt['url']==url and record['url']==safe_message(url) and record['job_id']==jid
            and record['owner_job_id']==origin['job_id'] and record['input_sha256']==origin['manifest_sha256']
            and record['transport_started'] is True and record['http_status'] is None
            and diag['source']==source and diag['request_scope']=='product' and diag['response_available'] is False):
        return False
    if not legacy:
        redirect=diag['redirect']
        return (diag['wire_outcome']=='REDIRECT_NOT_FOLLOWED'
                and (error['module'],error['type'],error['message'])==
                    ('universal_supplier.http_capture','UnfollowedProductRedirect','PRODUCT_REDIRECT_OUTSIDE_BATCH')
                and redirect['status'] in (301,302,303,307,308)
                and bool(re.fullmatch(r'[a-f0-9]{64}',redirect['target_sha256'])))
    # Old workers stopped before dispatching the new address. Require both
    # the exact first-stop event and the immutable job's final dispatch list.
    if (diag['wire_outcome']!='UNKNOWN' or
            (error['module'],error['type'],error['message'])!=('builtins','ValueError',FROZEN_BATCH_REFUSAL)):
        return False
    first_stop=json.loads((folder/'FIRST_STOP.json').read_bytes())
    result=json.loads((folder/'result.json').read_bytes())
    starts=result['canary']['product_requests']
    return (first_stop['job_id']==jid and first_stop['reason']==FROZEN_BATCH_REFUSAL
            and result['supplier_code']==source and result['input_sha256']==origin['manifest_sha256']
            and result['owner_job_id']==origin['job_id'] and result['termination_reason']==FROZEN_BATCH_REFUSAL
            and result['resume_uncertain']==[url] and bool(starts) and starts[-1]==url)


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def original_capture_present(receipt,entry):
    value=receipt.get('original_capture_metadata')
    if not isinstance(value,dict):return False
    try:
        fields=dict(value);fields['observed_at']=datetime.fromisoformat(fields['observed_at'])
        capture=HttpCapture(**fields)
        return capture.requested_url==entry['url'] and capture.fingerprint==receipt.get('capture_fingerprint')
    except (ValueError,TypeError,KeyError):return False


def quarantine_evidence_present(receipt,entry):
    category=receipt.get('isolation',{}).get('category')
    if category == MANUAL_STOP_INTERRUPTED:
        try:return _manual_stop_proven(receipt, entry)
        except (OSError,ValueError,KeyError,TypeError):return False
    if category==MANUAL_REDIRECT:
        try:return _manual_redirect_proven(receipt,entry)
        except (OSError,ValueError,KeyError,TypeError):return False
    if category in MANUAL_TRANSPORT_ERRORS:
        proof=receipt.get('timeout_evidence') or {}
        origin=receipt.get('origin') or {}
        try:
            from .source_runtime import job_dir
            name=proof['file'];jid=proof['job_id'];source=origin['source']
            if (source not in SUPPLIER_CONFIGS or type(jid) is not int or jid<1 or
                    len(name)!=69 or not name.endswith('.json') or
                    any(c not in '0123456789abcdef' for c in name[:-5])):return False
            raw=(job_dir(source,jid)/'transport-errors'/name).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=proof['sha256']:return False
            record=json.loads(raw);diag=record['diagnostics']
            return (receipt['url']==entry['url'] and record['url']==safe_message(entry['url'])
                    and record['job_id']==jid and record['owner_job_id']==origin['job_id']
                    and record['input_sha256']==origin['manifest_sha256']
                    and record['transport_started'] is True and record['http_status'] is None
                    and diag['source']==source and diag['request_scope'] in ('product','discovery')
                    and diag['response_available'] is False
                    and manual_transport_category(diag['original_exception'])==category)
        except (OSError,ValueError,KeyError,TypeError):return False
    if receipt.get('isolation',{}).get('category')=='operator_scope':
        from .source_recovery import approved_scope_item
        try:approved_scope_item(receipt,entry);return True
        except (PermissionError,ValueError,KeyError,OSError):return False
    if receipt.get('isolation',{}).get('category')!='http':return original_capture_present(receipt,entry)
    record=receipt.get('http_evidence') or {}
    try:
        return (record['url']==entry['url'] and type(record['http_status']) is int and
            400<=record['http_status']<=599 and record['http_status'] not in (403,429) and
            not record.get('error') and len(record['evidence_sha256'])==64 and
            hashlib.sha256(Path(record['file']).read_bytes()).hexdigest()==record['evidence_sha256'] and
            datetime.fromisoformat(record['observed_at']).tzinfo is not None)
    except (KeyError,ValueError,TypeError,OSError):return False

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
        if sql=='COMMITTED':state='UNCERTAIN' if receipt and receipt.get('state')=='QUARANTINED' else 'COMMITTED'
        elif sql!='NOT_FOUND' or receipt is None:state='UNCERTAIN'
        else:
            state=receipt.get('state','UNCERTAIN')
            if state not in STATES:raise ValueError('UNKNOWN_RECEIPT_STATE')
            if state=='COMMITTED':state='UNCERTAIN'
            if state=='HTTP_CAPTURED_NOT_COMMITTED' and not original_capture_present(receipt,entry):state='UNCERTAIN'
            if state=='QUARANTINED' and not quarantine_evidence_present(receipt,entry):state='UNCERTAIN'
            if state=='QUARANTINED' and not (receipt.get('isolation',{}).get('before_ingest') is True and
                    receipt['isolation'].get('category') in ('parse','identity','http','matching','operator_scope',
                                                            MANUAL_REDIRECT,MANUAL_STOP_INTERRUPTED,*MANUAL_TRANSPORT_ERRORS)):state='UNCERTAIN'
            if state=='QUARANTINED' and receipt['isolation']['category']=='matching':
                proof=receipt['isolation'].get('rejection_proof',{})
                if not (proof.get('rollback_verified') is True and proof.get('sql_absent') is True and
                        all(proof.get(k) is not None for k in ('source_product_id','catalog_product_id','sterbrust_product_id'))):state='UNCERTAIN'
        output.append({'entry':deepcopy(entry),'receipt':deepcopy(receipt),'state':state,'origin':dict(origin)})
    return output

def pending_manifest(original,classified,*,parent_job_id,parent_manifest_sha256,retry_failed=False,reauthorized_uncertain=frozenset()):
    """New pending-only manifest data; original snapshot remains byte-for-byte owned by caller."""
    expected={'source':original['supplier_code'],'job_id':parent_job_id,'manifest_sha256':parent_manifest_sha256}
    if any(r.get('origin')!=expected for r in classified):raise ValueError('CLASSIFICATION_OWNER_MISMATCH')
    known={e['url']:e for e in original['entries']}
    if len(known)!=len(original['entries']):raise ValueError('DUPLICATE_MANIFEST_URL')
    if len(classified)!=len(known) or {x['entry']['url'] for x in classified}!=set(known):raise ValueError('INCOMPLETE_OR_DUPLICATE_CLASSIFICATION')
    http=[];offline=[];uncertain=[];failed=[];quarantined=[]
    for row in classified:
        entry=row['entry'];state=row['state']
        if entry!=known[entry['url']]:raise ValueError('MANIFEST_ENTRY_CHANGED')
        if state=='NOT_REQUESTED' or (state=='FAILED' and retry_failed):http.append(deepcopy(entry))
        elif state=='HTTP_CAPTURED_NOT_COMMITTED':offline.append(deepcopy(row))
        elif state=='UNCERTAIN' and entry['url'] in reauthorized_uncertain:http.append(deepcopy(entry))
        elif state=='UNCERTAIN':uncertain.append(entry['url'])
        elif state=='FAILED':failed.append(entry['url'])
        elif state=='QUARANTINED':quarantined.append(deepcopy(row))
    return {'http_pending':{**deepcopy(original),'entries':http,'resume':{'parent_job_id':parent_job_id,'parent_manifest_sha256':parent_manifest_sha256,'mode':'PENDING_ONLY','discovery_complete':False}},
            'offline_pending':offline,'uncertain':uncertain,'failed_requires_decision':failed,
            'quarantined':quarantined,'automatic_resume_allowed':not uncertain and not failed}


def retryable_network_urls(state):
    return sorted(url for url,row in state['terminal'].items() if row['state']=='QUARANTINED'
                  and row['receipt'].get('isolation',{}).get('category') in MANUAL_TRANSPORT_ERRORS)


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
        self.expected_bytes=None
        self.state={'format':'source-discovery-checkpoint/v1','source':source,'job_id':job_id,'manifest_sha256':manifest_sha,'revision':0,'frontier':[],'inflight':{},'terminal':{},'visited':[],'discovery_receipts':{},'discovery_finished':False,'category_failures':[]}
        if self.path.exists():
            raw=self.path.read_bytes()
            existing=json.loads(raw);checksum=existing.pop('checksum')
            if digest(existing)!=checksum:raise ValueError('CHECKPOINT_CHECKSUM')
            for key in ('format','source','job_id','manifest_sha256'):
                if existing[key]!=self.state[key]:raise ValueError('CHECKPOINT_OWNER_MISMATCH')
            for url in list(existing['frontier'])+list(existing['inflight'])+list(existing['terminal'])+list(existing['visited']):self.validator(url)
            self.state=existing;self.expected=checksum;self.expected_bytes=raw
    def _save(self):
        guard=self.path.with_suffix('.writer.lock');fd=os.open(guard,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        try:
            raw = self.path.read_bytes() if self.path.exists() else None
            if raw is None and self.expected is not None:
                raise RuntimeError('CHECKPOINT_CHANGED_BY_ANOTHER_WRITER')
            if raw != self.expected_bytes:
                recorded=json.loads(raw)
                recorded_checksum=recorded.pop('checksum')
                if digest(recorded)!=recorded_checksum:raise ValueError('CHECKPOINT_CHECKSUM')
                if recorded_checksum!=self.expected:raise RuntimeError('CHECKPOINT_CHANGED_BY_ANOTHER_WRITER')
            self.state['revision']+=1;checksum=digest(self.state)
            self.expected_bytes=write_checkpoint(self.path,{**self.state,'checksum':checksum},durable=True,compact=True)
            self.expected=checksum
        finally:os.close(fd);guard.unlink()
    @contextmanager
    def _change(self):
        with self.lock:
            old=deepcopy(self.state);expected=self.expected;expected_bytes=self.expected_bytes
            try:
                yield
                self._save()
            except BaseException:
                self.state=old;self.expected=expected;self.expected_bytes=expected_bytes;raise
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
    def record_terminal(self,receipts,*,defer_to_tail=False):
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
                if defer_to_tail:
                    if (state!='QUARANTINED' or receipt.get('isolation',{}).get('category') not in MANUAL_TRANSPORT_ERRORS
                            or not quarantine_evidence_present(receipt,{'url':url}) or url in self.state['frontier']):
                        raise ValueError('DEFERRED_TIMEOUT_PROOF_REQUIRED')
                    self.state['frontier'].append(url)
    def return_not_requested(self,receipt):
        """Only an explicit pre-transport receipt may return a claim to frontier.

        A crash or a lost transport response remains UNCERTAIN. Never infer
        NOT_REQUESTED from attempts=0, process absence, or a missing capture.
        """
        with self._change():
            url=receipt['url'];self.validator(url)
            prior=self.state['terminal'].get(url,{})
            if (url in self.state['inflight'] and receipt.get('state')=='NOT_REQUESTED'
                    and receipt.get('transport_started') is False
                    and prior.get('receipt',{}).get('isolation',{}).get('category') in MANUAL_TRANSPORT_ERRORS):
                self.state['inflight'].pop(url)
                self.state['frontier'].insert(0,url)
                return
            if (url not in self.state['inflight'] or url in self.state['terminal'] and self.state['terminal'][url]['state']!='NOT_REQUESTED'
                    or receipt.get('state')!='NOT_REQUESTED' or receipt.get('transport_started') is not False):
                raise ValueError('NOT_REQUESTED_PROOF_REQUIRED')
            history=self.state['terminal'].get(url,{}).get('history',[])+[deepcopy(receipt)]
            self.state['terminal'][url]={'state':'NOT_REQUESTED','receipt':deepcopy(receipt),'history':history}
            self.state['inflight'].pop(url)
            if url not in self.state['frontier']:self.state['frontier'].insert(0,url)
    def reauthorize_uncertain(self,urls):
        """Operator-signed mixed recovery only: proven no-response UNCERTAIN
        claims re-enter the frontier; receipt history is preserved."""
        with self._change():
            for url in urls:
                self.validator(url)
                item=self.state['terminal'].get(url)
                if item is None or item['state']!='UNCERTAIN':raise ValueError('REAUTHORIZED_URL_NOT_UNCERTAIN')
                if url in self.state['inflight'] or url in self.state['frontier']:raise ValueError('REAUTHORIZED_URL_STATE_INVALID')
                entry={'url':url,'state':'NOT_REQUESTED','operator_reauthorization':True,
                    'origin':{'source':self.state['source'],'job_id':self.state['job_id'],'manifest_sha256':self.state['manifest_sha256']}}
                self.state['terminal'][url]={'state':'NOT_REQUESTED','receipt':dict(entry),
                    'history':item['history']+[entry]}
                self.state['frontier'].insert(0,url)
    def retry_network(self,urls):
        """Admit only proven, operator-selected network failures from a drained queue."""
        with self._change():
            if not urls or self.state['frontier'] or self.state['inflight'] or urls!=retryable_network_urls(self.state):
                raise ValueError('MANUAL_NETWORK_RETRY_SCOPE_CHANGED')
            for url in urls:
                self.validator(url)
                if not quarantine_evidence_present(self.state['terminal'][url]['receipt'],{'url':url}):
                    raise ValueError('DEFERRED_TIMEOUT_PROOF_REQUIRED')
            self.state['frontier'].extend(urls)
    def quarantine_unrequested(self,receipt):
        """Only reuse an approved prior refusal; never invent transport/UNCERTAIN."""
        from .source_recovery import approved_scope_item
        with self._change():
            url=receipt['url'];self.validator(url)
            if (url not in self.state['frontier'] or url in self.state['inflight'] or
                    receipt.get('state')!='QUARANTINED' or
                    receipt.get('isolation',{}).get('reused_refusal') is not True or
                    receipt.get('origin')!={'source':self.state['source'],'job_id':self.state['job_id'],'manifest_sha256':self.state['manifest_sha256']}):
                raise ValueError('REUSED_SCOPE_REFUSAL_REQUIRED')
            approved_scope_item(receipt,{'url':url})
            previous=self.state['terminal'].get(url,{'history':[]})
            if previous.get('state') not in (None,'NOT_REQUESTED'):raise ValueError('REUSED_SCOPE_CANNOT_DOWNGRADE')
            self.state['frontier'].remove(url)
            self.state['terminal'][url]={'state':'QUARANTINED','receipt':deepcopy(receipt),'history':previous['history']+[deepcopy(receipt)]}
    def complete_discovery_node(self,url,children,*,evidence_sha256,products=()):
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
            prior=self.state['terminal'].pop(url,None)
            receipt={'evidence_sha256':evidence_sha256,'children':list(children),'products':deepcopy(list(products))}
            if prior:receipt['history']=prior['history']
            self.state['discovery_receipts'][url]=receipt
    def finish_discovery(self,*,category_failures=()):
        with self._change():
            self.state['discovery_finished']=True;self.state['category_failures']=list(category_failures)
    @property
    def complete(self):
        return bool(self.state['discovery_finished'] and not self.state['category_failures'] and not self.state['frontier'] and not self.state['inflight'] and all(r['state']=='COMMITTED' for r in self.state['terminal'].values()))
