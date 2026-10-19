CREATE TABLE nf_texto (
    extraction_id UUID PRIMARY KEY,
    id_documento BIGINT NOT NULL,
    url VARCHAR NOT NULL,
    snapshot_id UUID NOT NULL REFERENCES snapshots(snapshot_id),
    snapshot_sha256 VARCHAR NOT NULL,
    path VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    extractor VARCHAR NOT NULL,
    extractor_version VARCHAR NOT NULL,
    texto VARCHAR NOT NULL,
    text_sha256 VARCHAR NOT NULL,
    needs_ocr BOOLEAN NOT NULL,
    page_count INTEGER NOT NULL CHECK (page_count >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    UNIQUE (id_documento, url, snapshot_id, extractor, extractor_version)
);

CREATE INDEX nf_texto_document_url_idx ON nf_texto (id_documento, url, created_at);

CREATE TABLE nf_texto_findings (
    extraction_id UUID NOT NULL REFERENCES nf_texto(extraction_id),
    finding_key VARCHAR NOT NULL REFERENCES findings(finding_key),
    created_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id),
    PRIMARY KEY (extraction_id, finding_key)
);
