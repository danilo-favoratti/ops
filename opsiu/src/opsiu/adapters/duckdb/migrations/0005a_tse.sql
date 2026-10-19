CREATE TABLE tse_imports (
    import_id UUID PRIMARY KEY,
    snapshot_id UUID NOT NULL REFERENCES snapshots(snapshot_id),
    year INTEGER NOT NULL CHECK (year IN (2024, 2026)),
    records_count BIGINT NOT NULL CHECK (records_count >= 0),
    loaded_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id)
);

CREATE TABLE tse_candidatos (
    import_id UUID NOT NULL REFERENCES tse_imports(import_id),
    snapshot_id UUID NOT NULL REFERENCES snapshots(snapshot_id),
    year INTEGER NOT NULL CHECK (year IN (2024, 2026)),
    candidate_id VARCHAR NOT NULL,
    cpf VARCHAR,
    nome_normalizado VARCHAR NOT NULL,
    uf VARCHAR NOT NULL,
    cargo VARCHAR NOT NULL,
    source_member VARCHAR NOT NULL,
    source_record_number BIGINT NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    PRIMARY KEY (import_id, source_member, source_record_number)
);

CREATE INDEX tse_candidates_cpf_idx ON tse_candidatos (year, cpf);
CREATE INDEX tse_candidates_name_idx ON tse_candidatos (year, nome_normalizado, uf);

CREATE VIEW tse_imports_current AS
SELECT i.*, s.sha256 AS source_sha256
FROM tse_imports i JOIN snapshots s USING (snapshot_id)
QUALIFY row_number() OVER (
    PARTITION BY i.year ORDER BY i.loaded_at DESC, i.import_id DESC
) = 1;

CREATE VIEW tse_candidatos_current AS
SELECT c.*
FROM tse_candidatos c JOIN tse_imports_current i USING (import_id);
