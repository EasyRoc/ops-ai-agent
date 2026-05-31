-- agent/db/migrations/001_init.sql

CREATE TABLE IF NOT EXISTS incidents (
    id              VARCHAR(64) PRIMARY KEY,
    service         VARCHAR(128) NOT NULL,
    env             VARCHAR(32) NOT NULL DEFAULT 'prod',
    severity        VARCHAR(16) NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'open',
    alert_name      VARCHAR(256),
    alert_value     VARCHAR(128),
    root_cause      TEXT,
    confidence      FLOAT,
    evidence        JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at     TIMESTAMP WITH TIME ZONE,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_incidents_service ON incidents(service);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at DESC);

CREATE TABLE IF NOT EXISTS executions (
    id              SERIAL PRIMARY KEY,
    incident_id     VARCHAR(64) REFERENCES incidents(id),
    action          VARCHAR(128) NOT NULL,
    operator        VARCHAR(64),
    status          VARCHAR(32) NOT NULL DEFAULT 'pending',
    result          JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at    TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_executions_incident ON executions(incident_id);

CREATE TABLE IF NOT EXISTS reports (
    id              SERIAL PRIMARY KEY,
    incident_id     VARCHAR(64) REFERENCES incidents(id),
    content         TEXT NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id              SERIAL PRIMARY KEY,
    incident_id     VARCHAR(64),
    actor           VARCHAR(64) NOT NULL,
    action          VARCHAR(128) NOT NULL,
    detail          JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_incident ON audit_logs(incident_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
