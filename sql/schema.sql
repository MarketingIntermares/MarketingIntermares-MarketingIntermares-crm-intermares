CREATE TABLE IF NOT EXISTS sync_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    records_read INTEGER DEFAULT 0,
    records_written INTEGER DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type TEXT NOT NULL,
    entity_key TEXT,
    payload JSONB
);
