"""Fail-closed failure scope, shared by source runtime and existing scheduler."""
ROBOTS_LOCAL=frozenset({'SOURCE_ROBOTS_UNAVAILABLE','ROBOTS_NOT_PROVEN','ROBOTS_NOT_TEXT',
    'ROBOTS_REQUIRES_SLOWER_PROFILE','ROBOTS_DISALLOW','ROBOTS_UNKNOWN'})


def failure_scope(reason,runtime):
    if runtime.get('global_stop_reason') or runtime.get('resume_uncertain') or runtime.get('resume_failed'):return 'system'
    if reason in ROBOTS_LOCAL or str(reason).startswith(('SOURCE_BLOCKED_HTTP_403','SOURCE_BLOCKED_HTTP_429',
        'SOURCE_BLOCKED_INTERSTITIAL','SOURCE_HTTP_CIRCUIT_BREAKER','SOURCE_NETWORK_CIRCUIT_OPEN',
        'SOURCE_CARD_ERROR_THRESHOLD','SOURCE_IDENTITY_MASS_FAILURE','SOURCE_RETRY_AFTER_HTTP_503')):return 'supplier'
    if reason in ('MAX_RUN_DURATION_EXCEEDED','PRODUCT_BATCH_BUDGET') and runtime.get('resume_available'):return 'resume'
    return 'system'
