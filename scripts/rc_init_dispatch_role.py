import json,secrets
from psycopg import sql
from rc_prepare_database import connect,OUT,TARGET
p=OUT/'private-runtime.json';cfg=json.loads(p.read_text(encoding='utf-8'));password=secrets.token_urlsafe(36)
with connect(TARGET) as c:
    c.execute(sql.SQL('CREATE ROLE app_dispatch LOGIN PASSWORD {}').format(sql.Literal(password)))
    c.execute('GRANT USAGE ON SCHEMA public,competitor TO app_dispatch')
    c.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public,competitor TO app_dispatch')
    c.execute('GRANT INSERT,UPDATE ON rc_replay_jobs TO app_dispatch')
    c.execute('GRANT USAGE,SELECT ON SEQUENCE rc_replay_jobs_id_seq TO app_dispatch')
cfg['app_dispatch']=password;p.write_text(json.dumps(cfg),encoding='utf-8')
