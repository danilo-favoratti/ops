CREATE TABLE findings (
    finding_key VARCHAR PRIMARY KEY,
    rule_id VARCHAR NOT NULL,
    rule_version VARCHAR NOT NULL,
    id_documento BIGINT NOT NULL,
    severity VARCHAR NOT NULL,
    amount DECIMAL(18, 2) NOT NULL,
    evidence_json JSON NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('active', 'superseded')),
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    source_file_id UUID NOT NULL REFERENCES source_files(source_file_id),
    record_sha256 VARCHAR NOT NULL,
    code_commit VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    superseded_at TIMESTAMPTZ,
    superseded_by_run_id UUID REFERENCES runs(run_id),
    UNIQUE (rule_id, rule_version, id_documento),
    CHECK ((status = 'active' AND superseded_at IS NULL
                              AND superseded_by_run_id IS NULL)
        OR (status = 'superseded' AND superseded_at IS NOT NULL
                                  AND superseded_by_run_id IS NOT NULL))
);

CREATE INDEX findings_rule_status_idx ON findings (rule_id, rule_version, status);
CREATE INDEX findings_documento_idx ON findings (id_documento);
