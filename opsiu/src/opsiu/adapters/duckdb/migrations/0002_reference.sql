CREATE TABLE caps (
    category_code INTEGER NOT NULL,
    "limit" DECIMAL(18, 2) NOT NULL,
    valid_from DATE NOT NULL,
    run_id UUID NOT NULL REFERENCES runs (run_id),
    step_id UUID NOT NULL REFERENCES steps (step_id),
    PRIMARY KEY (category_code, valid_from)
);

CREATE TABLE cota_uf (
    uf VARCHAR NOT NULL,
    value DECIMAL(18, 2) NOT NULL,
    valid_from DATE NOT NULL,
    run_id UUID NOT NULL REFERENCES runs (run_id),
    step_id UUID NOT NULL REFERENCES steps (step_id),
    PRIMARY KEY (uf, valid_from)
);

CREATE TABLE adicionais (
    role VARCHAR NOT NULL,
    value DECIMAL(18, 2) NOT NULL,
    valid_from DATE NOT NULL,
    run_id UUID NOT NULL REFERENCES runs (run_id),
    step_id UUID NOT NULL REFERENCES steps (step_id),
    PRIMARY KEY (role, valid_from)
);
