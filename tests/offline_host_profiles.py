"""Explicit synthetic policies for OFFLINE transports; never a runtime fallback."""
import json,os
from universal_supplier.control_plane.host_fetch import compile_policy,policy_digest,host_key
from universal_supplier.control_plane.rate_control import DomainRatePolicy

def approval_for(url):return {'host':host_key(url),'reference':'OFFLINE_SYNTHETIC_PROFILE_ONLY'}

def approved_policy(source,url,changes=None):
    if source in ('partner_st','optimum'):return compile_policy(source,url,changes)
    rate=DomainRatePolicy.from_mapping({'max_concurrency':1,'min_request_interval_seconds':20.,'request_timeout_seconds':20.,'max_attempts':1,'max_run_duration_seconds':300.,**(changes or {})})
    approval=approval_for(url)
    allowed=json.loads(os.environ.get('STERBRUST_APPROVED_HOST_POLICIES','{}'))
    allowed[approval['host']]=policy_digest(source,approval['host'],rate,approval['reference'])
    os.environ['STERBRUST_APPROVED_HOST_POLICIES']=json.dumps(allowed)
    return compile_policy(source,url,rate.snapshot(),approval=approval)
