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

CREATE INDEX resumos_deputado_input_idx ON resumos_deputado
    (numero_deputado_id, input_sha256, model, prompt_version);

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
