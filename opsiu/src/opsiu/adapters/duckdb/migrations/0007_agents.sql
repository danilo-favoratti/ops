CREATE TABLE agent_traces (
    agent_trace_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    finding_key VARCHAR REFERENCES findings(finding_key),
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

-- Private records are durable before the terminal invocation row exists.
-- A crash can therefore leave identifiable records without a terminal row.
CREATE TABLE agent_trace_records (
    record_id UUID PRIMARY KEY,
    agent_trace_id UUID NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    kind VARCHAR NOT NULL,
    content VARCHAR NOT NULL,
    content_bytes BIGINT NOT NULL,
    truncated BOOLEAN NOT NULL,
    metadata_json JSON NOT NULL,
    correlation_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX agent_trace_records_trace_idx
    ON agent_trace_records (agent_trace_id, created_at, record_id);

CREATE TABLE pareceres (
    parecer_id UUID PRIMARY KEY,
    finding_key VARCHAR NOT NULL REFERENCES findings(finding_key),
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
