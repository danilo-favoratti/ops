CREATE TABLE snapshots (
    snapshot_id UUID PRIMARY KEY,
    source VARCHAR NOT NULL,
    path VARCHAR NOT NULL UNIQUE,
    manifest_json JSON NOT NULL,
    url VARCHAR NOT NULL,
    status INTEGER NOT NULL CHECK (status BETWEEN 200 AND 299),
    headers_json JSON NOT NULL,
    sha256 VARCHAR NOT NULL CHECK (length(sha256) = 64),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    fetched_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(run_id),
    step_id UUID NOT NULL REFERENCES steps(step_id)
);

CREATE INDEX snapshots_source_time_idx
    ON snapshots (source, fetched_at, snapshot_id);
