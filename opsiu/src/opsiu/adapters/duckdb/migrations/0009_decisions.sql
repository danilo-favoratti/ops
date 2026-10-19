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

CREATE INDEX decisions_finding_time_idx
    ON decisions (finding_key, decided_at, decision_id);

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
