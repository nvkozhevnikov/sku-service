-- Extension-only DDL. No existing source row/version or metadata rewrite.
DO $$ BEGIN
 IF current_database()<>'sterbrust_competitor_intel' THEN RAISE EXCEPTION 'Wrong DB'; END IF;
END $$;
CREATE TABLE IF NOT EXISTS competitor_source_surfaces (
 id bigserial PRIMARY KEY,
 source_id bigint NOT NULL REFERENCES competitor_sources(id), canonical_url text NOT NULL,
 surface_kind text NOT NULL, title text, status text NOT NULL CHECK(status IN ('LIVE_CRAWLED','ROBOTS_BLOCKED','INDEX_ONLY','DISCOVERED_NOT_FETCHED','ERROR')),
 robots_disallowed boolean,
 fetch_status text CHECK(fetch_status IN ('PUBLIC_FETCH_OK','BROWSER_FETCH_OK','HTTP_BLOCKED','AUTH_REQUIRED','CAPTCHA_BLOCKED','ERROR')),
 evidence_class text NOT NULL CHECK(evidence_class IN ('LIVE_CRAWLED','ROBOTS_POLICY_EVIDENCE','EXTERNAL_INDEX_EVIDENCE','NAVIGATION_EVIDENCE','TRANSPORT_ERROR')),
 reason text NOT NULL, observed_at timestamptz NOT NULL, raw_capture_ref text,
 details jsonb NOT NULL, UNIQUE(source_id,canonical_url),
 CHECK(status <> 'INDEX_ONLY' OR evidence_class='EXTERNAL_INDEX_EVIDENCE')
);
