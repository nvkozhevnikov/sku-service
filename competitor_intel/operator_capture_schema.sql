-- Dedicated competitor database only. Extend status vocabulary, preserve rows.
DO $$ BEGIN
 IF current_database()<>'sterbrust_competitor_intel' THEN RAISE EXCEPTION 'Wrong DB'; END IF;
END $$;
CREATE TABLE IF NOT EXISTS competitor_source_surfaces (
 id bigserial PRIMARY KEY,source_id bigint NOT NULL REFERENCES competitor_sources(id),
 canonical_url text NOT NULL,surface_kind text NOT NULL,title text,status text NOT NULL,
 robots_disallowed boolean,fetch_status text,evidence_class text NOT NULL,reason text NOT NULL,
 observed_at timestamptz NOT NULL,raw_capture_ref text,details jsonb NOT NULL,
 UNIQUE(source_id,canonical_url),CHECK(status<>'INDEX_ONLY' OR evidence_class='EXTERNAL_INDEX_EVIDENCE')
);
ALTER TABLE competitor_source_surfaces DROP CONSTRAINT IF EXISTS competitor_source_surfaces_status_check;
ALTER TABLE competitor_source_surfaces ADD CONSTRAINT competitor_source_surfaces_status_check
 CHECK(status IN ('LIVE_CRAWLED','ROBOTS_BLOCKED','INDEX_ONLY','DISCOVERED_NOT_FETCHED','ERROR','OPERATOR_CAPTURED'));
ALTER TABLE competitor_source_surfaces DROP CONSTRAINT IF EXISTS competitor_source_surfaces_fetch_status_check;
ALTER TABLE competitor_source_surfaces ADD CONSTRAINT competitor_source_surfaces_fetch_status_check
 CHECK(fetch_status IN ('PUBLIC_FETCH_OK','BROWSER_FETCH_OK','HTTP_BLOCKED','AUTH_REQUIRED','CAPTCHA_BLOCKED','ERROR','OPERATOR_CAPTURE'));
ALTER TABLE competitor_source_surfaces DROP CONSTRAINT IF EXISTS competitor_source_surfaces_evidence_class_check;
ALTER TABLE competitor_source_surfaces ADD CONSTRAINT competitor_source_surfaces_evidence_class_check
 CHECK(evidence_class IN ('LIVE_CRAWLED','ROBOTS_POLICY_EVIDENCE','EXTERNAL_INDEX_EVIDENCE','NAVIGATION_EVIDENCE','TRANSPORT_ERROR','PUBLIC_BROWSER_EVIDENCE'));
CREATE TABLE IF NOT EXISTS competitor_operator_captures (
 id bigserial PRIMARY KEY,source_id bigint NOT NULL REFERENCES competitor_sources(id),
 page_version_id bigint REFERENCES competitor_page_versions(id),original_url text NOT NULL,
 canonical_url text NOT NULL,capture_time timestamptz NOT NULL,
 capture_method text NOT NULL CHECK(capture_method='OPERATOR_BROWSER_CAPTURE'),
 source_domain text NOT NULL CHECK(source_domain='youmet.ru'),content_hash char(64) NOT NULL,
 fetch_status text NOT NULL CHECK(fetch_status='OPERATOR_CAPTURE'),
 evidence_class text NOT NULL CHECK(evidence_class='PUBLIC_BROWSER_EVIDENCE'),
 sanitized_html_ref text NOT NULL,metadata jsonb NOT NULL,
 UNIQUE(source_id,canonical_url,content_hash,capture_time)
);
