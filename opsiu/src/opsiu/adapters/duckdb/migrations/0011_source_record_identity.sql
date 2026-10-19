-- Extend identity transactionally while preserving all referenced audit rows.

CREATE TEMP TABLE _identity_finding_records AS SELECT * FROM finding_records;

CREATE TEMP TABLE _identity_finding_supersessions AS SELECT * FROM finding_supersessions;

CREATE TEMP TABLE _identity_nf_texto_findings AS SELECT * FROM nf_texto_findings;

CREATE TEMP TABLE _identity_agent_traces AS SELECT * FROM agent_traces;

CREATE TEMP TABLE _identity_pareceres AS SELECT * FROM pareceres;

CREATE TEMP TABLE _identity_resumos_deputado AS SELECT * FROM resumos_deputado;

CREATE TEMP TABLE _identity_resumo_fontes AS SELECT * FROM resumo_fontes;

CREATE TEMP TABLE _identity_decisions AS SELECT * FROM decisions;

DROP VIEW findings_current;

DROP VIEW findings;

DROP TABLE decisions;

DROP TABLE resumo_fontes;

DROP TABLE resumos_deputado;

DROP TABLE pareceres;

DROP TABLE agent_traces;

DROP TABLE nf_texto_findings;

DROP TABLE finding_supersessions;

DROP TABLE finding_records;

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
    record_identity VARCHAR NOT NULL DEFAULT '',
    CHECK (record_identity = '' OR (id_documento <= 0 AND regexp_full_match(record_identity, '[0-9a-f]{64}'))),
    UNIQUE (rule_id, rule_version, id_documento, record_identity)
);

INSERT INTO finding_records SELECT *, '' FROM _identity_finding_records;

CREATE TABLE finding_supersessions (
    finding_key VARCHAR PRIMARY KEY REFERENCES finding_records(finding_key),
    superseded_at TIMESTAMPTZ NOT NULL,
    superseded_by_run_id UUID NOT NULL REFERENCES runs(run_id)
);

INSERT INTO finding_supersessions SELECT * FROM _identity_finding_supersessions;

CREATE TABLE nf_texto_findings (
    extraction_id UUID NOT NULL REFERENCES nf_texto(extraction_id),
    finding_key VARCHAR NOT NULL REFERENCES finding_records(finding_key),
    created_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    PRIMARY KEY (extraction_id, finding_key)
);

INSERT INTO nf_texto_findings SELECT * FROM _identity_nf_texto_findings;

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

INSERT INTO agent_traces SELECT * FROM _identity_agent_traces;

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

INSERT INTO pareceres SELECT * FROM _identity_pareceres;

CREATE TABLE resumos_deputado (
    summary_id UUID PRIMARY KEY,
    numero_deputado_id BIGINT NOT NULL,
    nome_parlamentar VARCHAR NOT NULL,
    agent_trace_id UUID NOT NULL UNIQUE REFERENCES agent_traces(agent_trace_id),
    model VARCHAR NOT NULL,
    prompt_name VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    prompt_sha256 VARCHAR NOT NULL,
    input_sha256 VARCHAR NOT NULL,
    input_json JSON NOT NULL,
    output_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id)
);

INSERT INTO resumos_deputado SELECT * FROM _identity_resumos_deputado;

CREATE TABLE resumo_fontes (
    summary_id UUID NOT NULL REFERENCES resumos_deputado(summary_id),
    finding_key VARCHAR NOT NULL REFERENCES finding_records(finding_key),
    parecer_id UUID NOT NULL REFERENCES pareceres(parecer_id),
    parecer_trace_id UUID NOT NULL REFERENCES agent_traces(agent_trace_id),
    source_file_id UUID NOT NULL REFERENCES source_files(source_file_id),
    source_sha256 VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL,
    PRIMARY KEY (summary_id, finding_key)
);

INSERT INTO resumo_fontes SELECT * FROM _identity_resumo_fontes;

CREATE TABLE decisions (
    decision_id UUID PRIMARY KEY,
    finding_key VARCHAR NOT NULL REFERENCES finding_records(finding_key),
    decision VARCHAR NOT NULL CHECK (decision IN ('aprovado', 'rejeitado', 'revisar')),
    reviewer VARCHAR NOT NULL CHECK (length(trim(reviewer)) > 0),
    comment VARCHAR NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id)
);

INSERT INTO decisions SELECT * FROM _identity_decisions;

CREATE INDEX finding_records_rule_version_idx
    ON finding_records (rule_id, rule_version);

CREATE INDEX finding_records_documento_idx ON finding_records (id_documento);

CREATE INDEX agent_traces_finding_idx
    ON agent_traces (finding_key, agent_id, prompt_version, model);

CREATE INDEX pareceres_finding_prompt_idx
    ON pareceres (finding_key, agent_id, prompt_version, model);

CREATE INDEX resumos_deputado_input_idx ON resumos_deputado
    (numero_deputado_id, input_sha256, model, prompt_version);

CREATE INDEX decisions_finding_time_idx
    ON decisions (finding_key, decided_at, decision_id);

CREATE VIEW findings AS
SELECT r.finding_key, r.rule_id, r.rule_version, r.id_documento, r.severity,
       r.amount, r.evidence_json,
       CASE WHEN s.finding_key IS NULL THEN 'active' ELSE 'superseded' END AS status,
       r.run_id, r.step_id, r.source_file_id, r.record_sha256, r.code_commit,
       r.created_at, s.superseded_at, s.superseded_by_run_id, r.record_identity
FROM finding_records r LEFT JOIN finding_supersessions s USING (finding_key);

CREATE VIEW findings_current AS
WITH latest_decisions AS (
    SELECT * FROM decisions
    QUALIFY row_number() OVER (
        PARTITION BY finding_key ORDER BY decided_at DESC, decision_id DESC
    ) = 1
)
SELECT f.*, d.decision_id, d.decision, d.reviewer, d.comment, d.decided_at,
       d.run_id AS decision_run_id, d.step_id AS decision_step_id
FROM findings f LEFT JOIN latest_decisions d USING (finding_key);

DROP TABLE _identity_finding_records;

DROP TABLE _identity_finding_supersessions;

DROP TABLE _identity_nf_texto_findings;

DROP TABLE _identity_agent_traces;

DROP TABLE _identity_pareceres;

DROP TABLE _identity_resumos_deputado;

DROP TABLE _identity_resumo_fontes;

DROP TABLE _identity_decisions;
