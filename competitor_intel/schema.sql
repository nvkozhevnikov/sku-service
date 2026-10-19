-- Dedicated competitor database ONLY; never part of supplier migrations.
DO $$ BEGIN
  IF current_database() <> 'sterbrust_competitor_intel' THEN
    RAISE EXCEPTION 'Dedicated competitor database required';
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS competitor_meta (singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), schema_version integer NOT NULL);
INSERT INTO competitor_meta VALUES (true,1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS competitor_sources (
 id bigserial PRIMARY KEY, code text NOT NULL UNIQUE, name text NOT NULL,
 base_url text NOT NULL, region text, enabled boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS competitor_runs (
 id bigserial PRIMARY KEY, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 since_date date NOT NULL, result jsonb, status text NOT NULL DEFAULT 'RUNNING'
);
CREATE TABLE IF NOT EXISTS competitor_pages (
 id bigserial PRIMARY KEY, source_id bigint NOT NULL REFERENCES competitor_sources(id),
 page_type text NOT NULL, canonical_url text NOT NULL, title text NOT NULL,
 published_at date, valid_from date, valid_to date, inclusion_reason text NOT NULL,
 first_seen_at timestamptz NOT NULL, last_seen_at timestamptz NOT NULL, current_content_hash text NOT NULL,
 UNIQUE(source_id, canonical_url)
);
CREATE TABLE IF NOT EXISTS competitor_page_versions (
 id bigserial PRIMARY KEY, page_id bigint NOT NULL REFERENCES competitor_pages(id), fetched_at timestamptz NOT NULL,
 content_hash text NOT NULL, title text NOT NULL, raw_text text NOT NULL, normalized_text text NOT NULL,
 raw_capture_ref text NOT NULL, extractor_version text NOT NULL, payload jsonb NOT NULL,
 UNIQUE(page_id, content_hash)
);
ALTER TABLE competitor_page_versions ADD COLUMN IF NOT EXISTS qa_status text NOT NULL DEFAULT 'UNREVIEWED'
 CHECK(qa_status IN ('UNREVIEWED','ACCEPTED','REJECTED_EXTRACTOR'));
UPDATE competitor_meta SET schema_version=2 WHERE singleton;
CREATE TABLE IF NOT EXISTS competitor_observations (
 id bigserial PRIMARY KEY, run_id bigint NOT NULL REFERENCES competitor_runs(id), page_id bigint NOT NULL REFERENCES competitor_pages(id),
 page_version_id bigint NOT NULL REFERENCES competitor_page_versions(id), observed_at timestamptz NOT NULL,
 observed_url text NOT NULL, raw_capture_ref text NOT NULL, raw_hash text NOT NULL, transport text NOT NULL,
 UNIQUE(run_id,page_id)
);
CREATE TABLE IF NOT EXISTS competitor_campaign_items (
 id bigserial PRIMARY KEY, page_version_id bigint NOT NULL REFERENCES competitor_page_versions(id), item_key text NOT NULL,
 campaign_name text, tab_title text, product_name text NOT NULL, product_url text, brand text, model text, category text,
 old_price numeric(20,2), new_price numeric(20,2), currency text NOT NULL,
 discount_amount numeric(20,2), discount_percent numeric(9,2), displayed_discount_raw text,
 price_type text NOT NULL CHECK(price_type IN ('PUBLIC','SALE','PERSONAL','AUTH_REQUIRED','FROM_PRICE','UNKNOWN','INSTALLMENT_MONTHLY','CREDIT_PAYMENT','BONUS','DISCOUNT_PERCENT_ONLY')),
 price_label_raw text, availability text, region text, priority text NOT NULL CHECK(priority IN ('HIGH','NORMAL','LOW')),
 observed_at timestamptz NOT NULL, evidence_html text NOT NULL, warnings jsonb NOT NULL,
 CHECK(old_price IS NULL OR old_price>0), CHECK(new_price IS NULL OR new_price>0),
 CHECK(old_price IS NULL OR new_price IS NULL OR old_price>new_price), UNIQUE(page_version_id,item_key)
);
CREATE TABLE IF NOT EXISTS competitor_ai_analysis (
 id bigserial PRIMARY KEY, page_version_id bigint NOT NULL REFERENCES competitor_page_versions(id), content_hash text NOT NULL,
 provider text NOT NULL, model text NOT NULL, prompt_version text NOT NULL,
 analysis_status text NOT NULL CHECK(analysis_status IN ('NOT_REQUESTED','PENDING','DONE','ERROR')),
 summary text, content_type text, topics jsonb, products_json jsonb, commercial_mechanic_json jsonb,
 strategic_signals_json jsonb, sterbrust_relevance text, reason text, raw_structured_result jsonb,
 analyzed_at timestamptz, error text, UNIQUE(page_version_id,provider,model,prompt_version)
);
CREATE INDEX IF NOT EXISTS competitor_items_url ON competitor_campaign_items(product_url);
CREATE INDEX IF NOT EXISTS competitor_observations_page ON competitor_observations(page_id,observed_at);
