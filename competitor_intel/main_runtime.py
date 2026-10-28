"""Explicit permanent competitor runtime; never inherit supplier credentials."""
import json,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'competitor-data/main-config.json'
RUNTIME=ROOT/'competitor-data/collection'

def configure():
    os.environ['STERBRUST_UNIFIED_RC']='YES'
    os.environ['STERBRUST_COMPETITOR_MAIN']='YES'
    os.environ.pop('STERBRUST_COMPETITOR_REHEARSAL',None)
    os.environ['PGCONNECT_TIMEOUT']='5'

def services():
    from .monitoring import MonitoringService,MonitoringRepository
    from .execution import CollectionService
    return MonitoringService(MonitoringRepository(CONFIG)),CollectionService(CONFIG,runtime=RUNTIME)

def session_secret():
    # Existing login/session identity, without rotating credentials or creating users.
    private=ROOT/'reports/integration/private-main-session.json'
    value=json.loads(private.read_text(encoding='utf-8'))['session_secret']
    if not isinstance(value,str) or len(value)<32:raise ValueError('EXISTING_SESSION_SECRET_REQUIRED')
    return value
