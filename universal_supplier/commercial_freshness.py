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
    if at is None:return {'state':'unknown','verified_at':None,'age_seconds':None}
    age=(now-at).total_seconds()
    return {'state':'fresh' if 0<=age<=ttl else 'unknown' if age<0 else 'stale',
            'verified_at':at.isoformat(),'age_seconds':max(0,age)}


def assess(row, policy, now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Freshness requires timezone-aware as_of')
    receipt=bool(row.get('capture_id') and row.get('capture_fingerprint') and row.get('http_status')==200
                 and row.get('receipt_identity_valid') is True
                 and not synthetic_receipt([row.get('capture_diagnostics'),row.get('extraction_evidence')]))
    observed=timestamp(row.get('observed_at')) if receipt else None
    try:
        price=Decimal(str(row.get('observed_price')))
        numeric=price.is_finite() and price>0 and bool(row.get('observed_currency'))
    except (InvalidOperation,TypeError,ValueError):numeric=False
    price_at=observed if numeric and row.get('price_state') in ('numeric_public','numeric') else None
    availability_at=observed if row.get('observed_availability') in ('in_stock','out_of_stock','incoming','preorder','backorder','discontinued') else None
    success=timestamp(row.get('source_last_success_at'))
    content=observed if observed is not None and success is not None and success>=observed else None
    return {'price':field_state(price_at,policy['price_ttl'],now),
            'availability':field_state(availability_at,policy['availability_ttl'],now),
            'content':field_state(content,policy['content_ttl'],now),
            'capture_id':row.get('capture_id') if receipt else None,
            'capture_fingerprint':row.get('capture_fingerprint') if receipt else None,
            'last_changed_at':row.get('last_changed_at'),
            'price_source':row.get('price_source'), 'receipt_valid':receipt}
