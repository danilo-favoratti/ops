-- Keep referenced finding identities immutable. DuckDB rewrites updates to
-- indexed columns (including foreign keys) as delete/insert, so supersession
-- belongs in an append-only relation, not an update to the referenced row.
-- The migration runner executes this entire file in one transaction.
CREATE TABLE finding_records (
    finding_key VARCHAR PRIMARY KEY,
    rule_id VARCHAR NOT NULL,
    rule_version VARCHAR NOT NULL,
    id_documento BIGINT NOT NULL,
    severity VARCHAR NOT NULL,
    amount DECIMAL(18, 2) NOT NULL,
    evidence_json JSON NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    source_file_id UUID NOT NULL REFERENCES source_files(source_file_id),
    record_sha256 VARCHAR NOT NULL,
    code_commit VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (rule_id, rule_version, id_documento)
);

INSERT INTO finding_records
SELECT finding_key, rule_id, rule_version, id_documento, severity, amount,
       evidence_json, run_id, step_id, source_file_id, record_sha256, code_commit,
       created_at FROM findings;

CREATE INDEX finding_records_rule_version_idx
    ON finding_records (rule_id, rule_version);
CREATE INDEX finding_records_documento_idx ON finding_records (id_documento);

CREATE TABLE finding_supersessions (
    finding_key VARCHAR PRIMARY KEY REFERENCES finding_records(finding_key),
    superseded_at TIMESTAMPTZ NOT NULL,
    superseded_by_run_id UUID NOT NULL REFERENCES runs(run_id)
);

INSERT INTO finding_supersessions
SELECT finding_key, superseded_at, superseded_by_run_id
FROM findings WHERE status = 'superseded';

-- Preserve all audit columns and IDs while retargeting foreign keys. Private
-- trace chunks, invoice text, snapshots, and source records are untouched.
CREATE TEMP TABLE _lifecycle_pdf_links AS SELECT * FROM nf_texto_findings;
CREATE TEMP TABLE _lifecycle_opinions AS SELECT * FROM pareceres;
CREATE TEMP TABLE _lifecycle_traces AS SELECT * FROM agent_traces;
DROP TABLE pareceres;
DROP TABLE agent_traces;
DROP TABLE nf_texto_findings;
DROP TABLE findings;

-- Keep the public read schema and its column order stable for all consumers.
CREATE VIEW findings AS
SELECT r.finding_key, r.rule_id, r.rule_version, r.id_documento, r.severity,
       r.amount, r.evidence_json,
       CASE WHEN s.finding_key IS NULL THEN 'active' ELSE 'superseded' END AS status,
       r.run_id, r.step_id, r.source_file_id, r.record_sha256, r.code_commit,
       r.created_at, s.superseded_at, s.superseded_by_run_id
FROM finding_records r LEFT JOIN finding_supersessions s USING (finding_key);

CREATE TABLE nf_texto_findings (
    extraction_id UUID NOT NULL REFERENCES nf_texto(extraction_id),
    finding_key VARCHAR NOT NULL REFERENCES finding_records(finding_key),
    created_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    PRIMARY KEY (extraction_id, finding_key)
);

CREATE TABLE agent_traces (
    agent_trace_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    finding_key VARCHAR REFERENCES finding_records(finding_key),
    agent_id VARCHAR NOT NULL,
    agent_version VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    prompt_name VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    prompt_sha256 VARCHAR NOT NULL,
    input_schema VARCHAR NOT NULL,
    output_schema VARCHAR NOT NULL,
    instructions VARCHAR NOT NULL,
    input_json JSON NOT NULL,
    output_json JSON,
    usage_json JSON NOT NULL,
    failure_json JSON,
    status VARCHAR NOT NULL CHECK (status IN ('succeeded', 'failed', 'blocked', 'cancelled')),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    duration_ms DOUBLE NOT NULL CHECK (duration_ms >= 0)
);

CREATE INDEX agent_traces_finding_idx
    ON agent_traces (finding_key, agent_id, prompt_version, model);

CREATE TABLE pareceres (
    parecer_id UUID PRIMARY KEY,
    finding_key VARCHAR NOT NULL REFERENCES finding_records(finding_key),
    agent_trace_id UUID NOT NULL UNIQUE REFERENCES agent_traces(agent_trace_id),
    agent_id VARCHAR NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('parecer', 'nota_fiscal')),
    prompt_version VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    output_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id)
);

CREATE INDEX pareceres_finding_prompt_idx
    ON pareceres (finding_key, agent_id, prompt_version, model);

INSERT INTO nf_texto_findings SELECT * FROM _lifecycle_pdf_links;
INSERT INTO agent_traces SELECT * FROM _lifecycle_traces;
INSERT INTO pareceres SELECT * FROM _lifecycle_opinions;
DROP TABLE _lifecycle_pdf_links;
DROP TABLE _lifecycle_traces;
DROP TABLE _lifecycle_opinions;
