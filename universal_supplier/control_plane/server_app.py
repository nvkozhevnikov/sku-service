"""Server compose entry (docker-compose.server.yml): the panel, plus read-only
competitor monitoring when STERBRUST_COMPETITOR_SERVER=YES.

No competitor collector is attached here: the web process never fetches
competitor sites. Monitoring connects as competitor_runtime (SELECT only on the
competitor schema) and Store verifies role, search_path and cluster identity.
"""
import os

os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP'] = 'YES'


def build_app():
    from .web import create_app

    competitor = None
    if os.environ.get('STERBRUST_COMPETITOR_SERVER') == 'YES':
        from psycopg.conninfo import make_conninfo
        from competitor_intel.monitoring import MonitoringRepository, MonitoringService
        from competitor_intel.store import server_identity

        os.environ['STERBRUST_UNIFIED_RC'] = 'YES'
        os.environ['STERBRUST_COMPETITOR_MAIN'] = 'YES'
        os.environ.pop('STERBRUST_COMPETITOR_REHEARSAL', None)
        target = server_identity()
        password = os.environ.get('COMPETITOR_DB_PASSWORD', '')
        if len(password) < 16:
            raise RuntimeError('COMPETITOR_DB_PASSWORD required for competitor monitoring')
        dsn = make_conninfo(host=target['host'], port=target['port'], dbname=target['dbname'],
                            user=target['user'], password=password,
                            sslmode=os.environ.get('DB_SSLMODE', 'disable'))
        competitor = MonitoringService(MonitoringRepository(dsn=dsn))
    return create_app(competitor_service=competitor)


try:
    app = build_app()
except RuntimeError as error:
    from .web import create_unavailable_app
    app = create_unavailable_app(str(error))
