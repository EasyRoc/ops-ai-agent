ALTER TABLE reports
    ADD COLUMN IF NOT EXISTS fault_patterns JSONB;

ALTER TABLE executions
    ALTER COLUMN action TYPE VARCHAR(512);

CREATE INDEX IF NOT EXISTS idx_reports_incident ON reports(incident_id);
