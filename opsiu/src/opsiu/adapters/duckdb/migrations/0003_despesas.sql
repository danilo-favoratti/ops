ALTER TABLE source_files ADD COLUMN source_year INTEGER;

CREATE TABLE despesas (
    source_file_id UUID NOT NULL REFERENCES source_files(source_file_id),
    source_record_number BIGINT NOT NULL CHECK (source_record_number > 0),
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    record_sha256 VARCHAR NOT NULL,
    nome_parlamentar VARCHAR NOT NULL,
    cpf VARCHAR,
    numero_carteira_parlamentar BIGINT,
    legislatura BIGINT,
    sigla_uf VARCHAR NOT NULL,
    sigla_partido VARCHAR,
    codigo_legislatura BIGINT,
    numero_sub_cota BIGINT NOT NULL,
    descricao VARCHAR,
    numero_especificacao_sub_cota BIGINT,
    descricao_especificacao VARCHAR,
    fornecedor VARCHAR,
    cnpj_cpf_digits VARCHAR,
    payer_kind VARCHAR NOT NULL CHECK (payer_kind IN ('CNPJ', 'CPF', 'OUTRO')),
    numero VARCHAR,
    tipo_documento BIGINT,
    data_emissao DATE,
    valor_documento DECIMAL(18, 2) NOT NULL,
    valor_glosa DECIMAL(18, 2) NOT NULL,
    valor_liquido DECIMAL(18, 2) NOT NULL,
    ano INTEGER NOT NULL,
    mes INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    parcela BIGINT,
    passageiro VARCHAR,
    trecho VARCHAR,
    lote BIGINT,
    ressarcimento BIGINT,
    data_pagamento_restituicao DATE,
    restituicao DECIMAL(18, 2),
    numero_deputado_id BIGINT NOT NULL,
    id_documento BIGINT NOT NULL,
    url_documento VARCHAR,
    PRIMARY KEY (source_file_id, source_record_number)
);

CREATE INDEX despesas_deputado_competencia_idx
    ON despesas (numero_deputado_id, ano, mes);
CREATE INDEX despesas_supplier_idx ON despesas (cnpj_cpf_digits);
CREATE INDEX despesas_documento_idx ON despesas (id_documento);

-- Choose the source first: an empty replacement also supersedes prior rows.
CREATE VIEW despesas_current AS
WITH current_sources AS (
    SELECT source_file_id FROM source_files
    WHERE source_year IS NOT NULL
    QUALIFY row_number() OVER (
        PARTITION BY source_year ORDER BY ingested_at DESC, source_file_id DESC
    ) = 1
)
SELECT despesas.* FROM despesas JOIN current_sources USING (source_file_id);

CREATE TABLE deputados (
    numero_deputado_id BIGINT PRIMARY KEY,
    nome VARCHAR NOT NULL,
    cpf VARCHAR,
    uf VARCHAR NOT NULL,
    partido VARCHAR,
    legislatura BIGINT,
    codigo_legislatura BIGINT,
    numero_carteira_parlamentar BIGINT,
    lideranca BOOLEAN NOT NULL,
    source_file_id UUID NOT NULL REFERENCES source_files(source_file_id),
    source_record_number BIGINT NOT NULL,
    id_documento BIGINT NOT NULL,
    record_sha256 VARCHAR NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id)
);
