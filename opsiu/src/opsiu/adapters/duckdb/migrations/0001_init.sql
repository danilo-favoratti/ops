CREATE TABLE schema_migrations (
    migration_id VARCHAR PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE runs (
    run_id UUID PRIMARY KEY,
    command VARCHAR NOT NULL,
    args_json JSON NOT NULL,
    settings_hash VARCHAR NOT NULL,
    code_commit VARCHAR NOT NULL,
    code_dirty BOOLEAN NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR NOT NULL CHECK (
        status IN ('running', 'completed', 'completed_with_errors', 'aborted')
    ),
    counters_json JSON NOT NULL DEFAULT '{}'
);

CREATE TABLE steps (
    step_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs (run_id),
    name VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR NOT NULL CHECK (
        status IN ('running', 'completed', 'failed', 'skipped')
    )
);

CREATE TABLE events (
    event_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs (run_id),
    step_id UUID REFERENCES steps (step_id),
    kind VARCHAR NOT NULL,
    attributes_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE source_files (
    source_file_id UUID PRIMARY KEY,
    path VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    size BIGINT NOT NULL CHECK (size >= 0),
    ingested_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES runs (run_id)
);
