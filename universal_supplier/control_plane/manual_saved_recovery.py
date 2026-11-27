"""Exact recovery of two known manual REFRESH failures using local evidence."""

import hashlib
import json
import re
from pathlib import Path

from universal_supplier.commercial_persistence import OPTIMUM_PUBLIC_LICENSE_URL
from universal_supplier.http_capture import public_transport_url
from .source_resume import FROZEN_BATCH_REFUSAL
from .transport_diagnostics import safe_message

WIRE_ERROR = 'ProductTargetOutsideBatch:' + FROZEN_BATCH_REFUSAL
ARCHIVE_ERROR = 'ValueError:capture URL must not retain query or credential data'
LTT_SOURCES = ('ltt_msk', 'ltt_sib')


def make_wire_receipt(old, parent_id, path):
    receipt = {'url':old['url'],'origin':old['origin'],'state':'NOT_REQUESTED',
               'transport_started':False,'wire_evidence':{'job_id':parent_id,
               'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}}
    if not proven_wire_refusal(receipt, {'url':old['url']}):
        raise ValueError('MANUAL_WIRE_REFUSAL_NOT_PROVEN')
    return receipt


def proven_wire_refusal(receipt, entry):
    """The pre-dispatch Unicode refusal is distinct from an unseen HTTP result."""
    from .source_runtime import job_dir

    try:
        origin, proof, url = receipt['origin'], receipt['wire_evidence'], entry['url']
        code, parent, filename = origin['source'], proof['job_id'], proof['file']
        if (code not in LTT_SOURCES or type(parent) is not int or parent < origin['job_id']
                or not re.fullmatch(r'[0-9a-f]{64}\.json',filename) or url != receipt['url']):
            return False
        wire = public_transport_url(url)
        if url == wire:
            return False
        folder = job_dir(code,parent)
        raw = (folder/'transport-errors'/filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != proof['sha256']:
            return False
        failed = json.loads(raw)
        diagnostic = failed['diagnostics']
        exception = diagnostic['original_exception']
        result = json.loads((folder/'result.json').read_bytes())
        dispatched = result['canary']['product_requests']
        return (
            failed['job_id']==parent and failed['owner_job_id']==origin['job_id']
            and failed['input_sha256']==origin['manifest_sha256'] and failed['url']==safe_message(url)
            and failed['http_status'] is None and diagnostic['source']==code
            and diagnostic['request_scope']=='product' and diagnostic['response_available'] is False
            and diagnostic['wire_outcome']=='UNKNOWN'
            and (exception['module'],exception['type'],exception['message'])==
                ('universal_supplier.http_capture','ProductTargetOutsideBatch',FROZEN_BATCH_REFUSAL)
            and result['termination_reason']=='SOURCE_CARD_HTTP_FAILED'
            and result['owner_job_id']==origin['job_id'] and result['input_sha256']==origin['manifest_sha256']
            and result['supplier_code']==code and result['resume_uncertain']==[url]
            and isinstance(dispatched,list) and url not in dispatched and wire not in dispatched
        )
    except (OSError,ValueError,TypeError,KeyError):
        return False


def optimum_archive_receipt(old, path):
    from .source_runtime import capture_from_record, job_dir, receipt_for

    origin = old['origin']
    owner, sha = origin['job_id'], origin['manifest_sha256']
    folder = job_dir('optimum',owner)
    if (old['url'] != OPTIMUM_PUBLIC_LICENSE_URL or origin['source'] != 'optimum'
            or not re.fullmatch(r'[0-9a-f]{64}\.json', path.name)
            or path.parent != folder/'responses'):
        raise ValueError('MANUAL_SAVED_CAPTURE_NOT_PROVEN')
    raw = path.read_bytes()
    record = json.loads(raw)
    evidence_sha = record['evidence_sha256']
    body = folder/'captures'/(evidence_sha+'.html')
    if (record['origin_job_id'] != owner or record['input_sha256'] != sha
            or record['url'] != old['url'] or record['final_url'] != old['url']
            or record['http_status'] != 200 or record['error'] or record['redirects']
            or not re.fullmatch(r'[0-9a-f]{64}',evidence_sha) or body != Path(record['file'])
            or hashlib.sha256(body.read_bytes()).hexdigest() != evidence_sha):
        raise ValueError('MANUAL_SAVED_CAPTURE_NOT_PROVEN')
    receipt = receipt_for(old['url'],'HTTP_CAPTURED_NOT_COMMITTED',capture_from_record(record),
                          'optimum',owner,sha)
    receipt['saved_response'] = {'file':path.name,'sha256':hashlib.sha256(raw).hexdigest()}
    return receipt
