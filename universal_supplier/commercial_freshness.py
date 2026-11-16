"""Receipt-backed field freshness. Never rewrites historical values or availability."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

def synthetic_receipt(value):
    if isinstance(value,dict):
        return any((key in ('synthetic','is_synthetic') and item is True) or synthetic_receipt(item) for key,item in value.items())
    if isinstance(value,(list,tuple)):return any(synthetic_receipt(item) for item in value)
    return isinstance(value,str) and ('SYNTHETIC' in value.upper() or 'NOT_HTTP' in value.upper())


def timestamp(value):
    if isinstance(value,str):
        try:value=datetime.fromisoformat(value)
        except ValueError:return None
    return value if isinstance(value,datetime) and value.tzinfo is not None else None


def field_state(at, ttl, now):
    at=timestamp(at)
    if at is None:return {'state':'unknown','verified_at':None,'age_seconds':None,'ttl_seconds':ttl,
                          'classification':'unknown'}
    age=(now-at).total_seconds()
    state='fresh' if 0<=age<=ttl else 'unknown' if age<0 else 'stale'
    return {'state':state,'verified_at':at.isoformat(),'age_seconds':max(0,age),'ttl_seconds':ttl,
            'classification':'trusted_current_observation' if state=='fresh' else
                'last_known_historical_for_diagnostics' if state=='stale' else 'unknown'}


def assess(row, policy, now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Freshness requires timezone-aware as_of')
    receipt_issues=[]
    if not row.get('capture_id'):receipt_issues.append('CAPTURE_MISSING')
    if not row.get('capture_fingerprint'):receipt_issues.append('CAPTURE_FINGERPRINT_MISSING')
    if row.get('http_status')!=200:receipt_issues.append('HTTP_200_NOT_PROVEN')
    if row.get('receipt_identity_valid') is not True:receipt_issues.append('RECEIPT_IDENTITY_NOT_PROVEN')
    if synthetic_receipt([row.get('capture_diagnostics'),row.get('extraction_evidence')]):receipt_issues.append('SYNTHETIC_NOT_LIVE_EVIDENCE')
    receipt=not receipt_issues
    observed=timestamp(row.get('observed_at')) if receipt else None
    try:
        price=Decimal(str(row.get('observed_price')))
        numeric=price.is_finite() and price>0 and bool(row.get('observed_currency'))
    except (InvalidOperation,TypeError,ValueError):numeric=False
    extraction=row.get('extraction_evidence') or {}
    exact=(extraction.get('price_basis')!='from_price' and extraction.get('variant_or_group') is not True
           and not row.get('unusable_price_reasons'))
    price_at=observed if numeric and exact and row.get('price_state') in ('numeric_public','numeric') else None
    availability_at=observed if row.get('observed_availability') in ('in_stock','out_of_stock','incoming','preorder','backorder','discontinued') else None
    success=timestamp(row.get('source_last_success_at'))
    content=observed if observed is not None and success is not None and success>=observed and not extraction.get('commercial_only') else None
    return {'price':field_state(price_at,policy['price_ttl'],now),
            'availability':field_state(availability_at,policy['availability_ttl'],now),
            'content':field_state(content,policy['content_ttl'],now),
            'capture_id':row.get('capture_id') if receipt else None,
            'capture_fingerprint':row.get('capture_fingerprint') if receipt else None,
            'last_changed_at':row.get('last_changed_at'),
            'price_source':row.get('price_source'), 'receipt_valid':receipt,'receipt_issues':receipt_issues}
