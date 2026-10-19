CREATE TABLE cnpj_snapshots (
    snapshot_id UUID PRIMARY KEY,
    path VARCHAR NOT NULL UNIQUE,
    source_directory VARCHAR NOT NULL,
    manifest_json JSON NOT NULL,
    sha256 VARCHAR NOT NULL CHECK (length(sha256) = 64),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    captured_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id)
);

CREATE TABLE cnpj_imports (
    import_id UUID PRIMARY KEY,
    snapshot_id UUID NOT NULL REFERENCES cnpj_snapshots(snapshot_id),
    roots_sha256 VARCHAR NOT NULL,
    companies BIGINT NOT NULL,
    partners BIGINT NOT NULL,
    establishments BIGINT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id)
);

CREATE TABLE cnpj_import_roots (
    import_id UUID NOT NULL REFERENCES cnpj_imports(import_id),
    cnpj_root VARCHAR NOT NULL,
    PRIMARY KEY (import_id, cnpj_root)
);

CREATE TABLE cnpj_empresas (
    import_id UUID NOT NULL REFERENCES cnpj_imports(import_id),
    snapshot_id UUID NOT NULL REFERENCES cnpj_snapshots(snapshot_id),
    cnpj_root VARCHAR NOT NULL,
    razao_social VARCHAR NOT NULL,
    natureza_juridica VARCHAR NOT NULL,
    source_file VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    source_member VARCHAR NOT NULL,
    source_record_number BIGINT NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    PRIMARY KEY (import_id, cnpj_root)
);

CREATE TABLE cnpj_socios (
    import_id UUID NOT NULL REFERENCES cnpj_imports(import_id),
    snapshot_id UUID NOT NULL REFERENCES cnpj_snapshots(snapshot_id),
    cnpj_root VARCHAR NOT NULL,
    tipo_socio INTEGER NOT NULL,
    nome_socio VARCHAR NOT NULL,
    qualificacao VARCHAR NOT NULL,
    source_file VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    source_member VARCHAR NOT NULL,
    source_record_number BIGINT NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    PRIMARY KEY (import_id, source_file, source_member, source_record_number)
);

CREATE TABLE cnpj_estabelecimentos (
    import_id UUID NOT NULL REFERENCES cnpj_imports(import_id),
    snapshot_id UUID NOT NULL REFERENCES cnpj_snapshots(snapshot_id),
    cnpj_root VARCHAR NOT NULL,
    cnpj VARCHAR NOT NULL,
    cnae_principal VARCHAR,
    source_file VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    source_member VARCHAR NOT NULL,
    source_record_number BIGINT NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    PRIMARY KEY (import_id, cnpj)
);

CREATE INDEX cnpj_partners_root_idx ON cnpj_socios (cnpj_root);

CREATE VIEW cnpj_imports_current AS
SELECT i.*, s.sha256 AS source_sha256
FROM cnpj_imports i JOIN cnpj_snapshots s USING (snapshot_id)
ORDER BY i.loaded_at DESC, i.import_id DESC LIMIT 1;

CREATE VIEW cnpj_import_roots_current AS
SELECT r.* FROM cnpj_import_roots r JOIN cnpj_imports_current i USING (import_id);

CREATE VIEW cnpj_empresas_base_current AS
SELECT c.* FROM cnpj_empresas c JOIN cnpj_imports_current i USING (import_id);

CREATE VIEW cnpj_socios_current AS
SELECT c.* FROM cnpj_socios c JOIN cnpj_imports_current i USING (import_id);

CREATE VIEW cnpj_estabelecimentos_current AS
SELECT c.* FROM cnpj_estabelecimentos c JOIN cnpj_imports_current i USING (import_id);

-- Primary CNAE belongs to each full establishment CNPJ, not the company root.
CREATE VIEW cnpj_empresas_current AS
SELECT c.*, e.cnpj, e.cnae_principal,
    e.source_file AS establishment_source_file,
    e.source_sha256 AS establishment_source_sha256,
    e.source_member AS establishment_source_member,
    e.source_record_number AS establishment_source_record_number
FROM cnpj_empresas_base_current c
LEFT JOIN cnpj_estabelecimentos_current e USING (import_id, cnpj_root);
