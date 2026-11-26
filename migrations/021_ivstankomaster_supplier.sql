-- Register a passive namespace. Supplier activation stays under the operator's control.
INSERT INTO suppliers (code, name, base_url, adapter_name, enabled, transport_mode, crawl_settings, rate_settings)
VALUES (
    'ivstankomaster', 'Ивстанкомастер', 'https://ivstankomaster.ru/', 'ivstankomaster', false, 'direct',
    '{"safety_gate_size": 3, "max_discovery_pages": 30, "max_run_duration_seconds": 900}'::jsonb,
    '{"max_concurrency": 1, "min_request_interval_seconds": 2, "jitter_min_seconds": 0,
      "jitter_max_seconds": 1, "request_timeout_seconds": 20, "max_attempts": 1,
      "max_run_duration_seconds": 900}'::jsonb
)
ON CONFLICT (code) DO NOTHING;
