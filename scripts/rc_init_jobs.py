from rc_prepare_database import connect,TARGET
with connect(TARGET) as c:
    c.execute("""CREATE TABLE rc_replay_jobs(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      source_key text NOT NULL,origin text NOT NULL,due_slot timestamptz,
      status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed')),
      created_at timestamptz NOT NULL DEFAULT now(),started_at timestamptz,finished_at timestamptz,result jsonb,
      UNIQUE(source_key,due_slot))""")
    c.execute("CREATE UNIQUE INDEX rc_replay_one_active ON rc_replay_jobs(source_key) WHERE status IN ('queued','running')")
    c.execute('GRANT SELECT,INSERT,UPDATE ON rc_replay_jobs TO app_supplier')
    c.execute('GRANT SELECT ON rc_replay_jobs TO reporting_ro')
    c.execute('GRANT USAGE,SELECT ON SEQUENCE rc_replay_jobs_id_seq TO app_supplier')
