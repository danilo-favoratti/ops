"""DuckDB persistence implementations for application repository ports."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from duckdb import DuckDBPyConnection

from opsiu.application.ports import ExpenseSourceMetadata
from opsiu.domain.errors import DomainError
from opsiu.domain.models import Despesa
from opsiu.domain.policies import Adicional, Cap, CotaUF


class DuckDBPolicyRepository:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def upsert_policies(
        self,
        caps: tuple[Cap, ...],
        quotas: tuple[CotaUF, ...],
        additions: tuple[Adicional, ...],
        *,
        run_id: str,
        step_id: str,
    ) -> None:
        """Atomically insert policy versions without changing existing history."""
        active = self.connection.execute(
            "SELECT 1 FROM runs JOIN steps USING (run_id) "
            "WHERE runs.run_id = ? AND steps.step_id = ? "
            "AND runs.status = 'running' AND steps.status = 'running'",
            [run_id, step_id],
        ).fetchone()
        if active is None:
            raise RuntimeError("policy writes require a matching active run and step")
        self.connection.execute("BEGIN TRANSACTION")
        try:
            for cap in caps:
                self._insert_version(
                    "caps",
                    "category_code",
                    '"limit"',
                    cap.category_code,
                    cap.limit,
                    cap.valid_from,
                    run_id,
                    step_id,
                )
            for quota in quotas:
                self._insert_version(
                    "cota_uf",
                    "uf",
                    "value",
                    quota.uf,
                    quota.value,
                    quota.valid_from,
                    run_id,
                    step_id,
                )
            for addition in additions:
                self._insert_version(
                    "adicionais",
                    "role",
                    "value",
                    addition.role,
                    addition.value,
                    addition.valid_from,
                    run_id,
                    step_id,
                )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _insert_version(
        self,
        table: str,
        key_column: str,
        value_column: str,
        key: int | str,
        value: Decimal,
        valid_from: date,
        run_id: str,
        step_id: str,
    ) -> None:
        # Table and column identifiers are fixed in the calls above; values are bound.
        existing = self.connection.execute(
            f"SELECT {value_column} FROM {table} "
            f"WHERE {key_column} = ? AND valid_from = ?",
            [key, valid_from],
        ).fetchone()
        if existing is not None:
            if existing[0] != value:
                raise DomainError(
                    f"conflicting policy version: {table}/{key}/"
                    f"{valid_from.isoformat()}"
                )
            return
        self.connection.execute(
            f"INSERT INTO {table} "
            f"({key_column}, {value_column}, valid_from, run_id, step_id) "
            "VALUES (?, ?, ?, ?, ?)",
            [key, value, valid_from, run_id, step_id],
        )


# Column order is explicit so inserting a new domain field cannot silently shift
# stored values. Competencia is flattened and the normalized payee has a SQL name.
EXPENSE_COLUMNS = (
    "source_file_id",
    "source_record_number",
    "run_id",
    "step_id",
    "record_sha256",
    "nome_parlamentar",
    "cpf",
    "numero_carteira_parlamentar",
    "legislatura",
    "sigla_uf",
    "sigla_partido",
    "codigo_legislatura",
    "numero_sub_cota",
    "descricao",
    "numero_especificacao_sub_cota",
    "descricao_especificacao",
    "fornecedor",
    "cnpj_cpf_digits",
    "payer_kind",
    "numero",
    "tipo_documento",
    "data_emissao",
    "valor_documento",
    "valor_glosa",
    "valor_liquido",
    "ano",
    "mes",
    "parcela",
    "passageiro",
    "trecho",
    "lote",
    "ressarcimento",
    "data_pagamento_restituicao",
    "restituicao",
    "numero_deputado_id",
    "id_documento",
    "url_documento",
)


class DuckDBExpenseRepository:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def current_source_sha256(self, source_year: int) -> str | None:
        row = self.connection.execute(
            "SELECT sha256 FROM source_files WHERE source_year = ? "
            "ORDER BY ingested_at DESC, source_file_id DESC LIMIT 1",
            [source_year],
        ).fetchone()
        return str(row[0]) if row else None

    @contextmanager
    def stage(
        self, *, source_file_id: str, source_year: int, run_id: str, step_id: str
    ) -> Iterator[DuckDBExpenseStage]:
        active = self.connection.execute(
            "SELECT 1 FROM runs JOIN steps USING (run_id) "
            "WHERE runs.run_id = ? AND steps.step_id = ? "
            "AND runs.status = 'running' AND steps.status = 'running'",
            [run_id, step_id],
        ).fetchone()
        if active is None:
            raise RuntimeError("expense writes require a matching active run and step")
        stage = DuckDBExpenseStage(
            self.connection, source_file_id, source_year, run_id, step_id
        )
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.connection.execute(
                f"CREATE TEMP TABLE {stage.table} AS SELECT * FROM despesas LIMIT 0"
            )
            yield stage
        finally:
            try:
                if not stage.published:
                    self.connection.execute("ROLLBACK")
            finally:
                stage.closed = True


class DuckDBExpenseStage:
    def __init__(
        self,
        connection: DuckDBPyConnection,
        source_file_id: str,
        source_year: int,
        run_id: str,
        step_id: str,
    ) -> None:
        self.connection = connection
        self.source_file_id = source_file_id
        self.source_year = source_year
        self.run_id = run_id
        self.step_id = step_id
        self.table = "ingest_stage_" + UUID(source_file_id).hex
        self.json_schema = json.dumps(
            [
                {
                    str(row[0]): str(row[1])
                    for row in connection.execute("DESCRIBE despesas").fetchall()
                }
            ]
        )
        self.count = 0
        self.published = False
        self.closed = False

    def _require_open(self) -> None:
        if self.closed or self.published:
            raise RuntimeError("expense stage is no longer open")

    def add_batch(self, expenses: tuple[Despesa, ...]) -> None:
        self._require_open()
        if any(expense.ano != self.source_year for expense in expenses):
            raise DomainError("expense year does not match the yearly source")
        if not expenses:
            return
        rows: list[dict[str, object]] = []
        for offset, expense in enumerate(expenses, start=self.count + 1):
            rows.append(
                dict(
                    zip(
                        EXPENSE_COLUMNS,
                        [
                            self.source_file_id,
                            offset,
                            self.run_id,
                            self.step_id,
                            *[
                                getattr(expense, column)
                                for column in EXPENSE_COLUMNS[4:]
                            ],
                        ],
                        strict=True,
                    )
                )
            )
        # Bind one bounded JSON batch: scalar Python bindings otherwise repeatedly
        # probe optional dataframe dependencies. Strict SQL casts preserve exact
        # decimals/dates and reject overflow rather than producing silent NULLs.
        self.connection.execute(
            f"INSERT INTO {self.table} ({', '.join(EXPENSE_COLUMNS)}) "
            "SELECT row.* FROM (SELECT unnest(from_json_strict(?, ?)) AS row)",
            [
                json.dumps(
                    rows,
                    default=_json_scalar,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ),
                self.json_schema,
            ],
        )
        self.count += len(expenses)

    def publish(
        self, metadata: ExpenseSourceMetadata, *, ingested_at: datetime
    ) -> None:
        self._require_open()
        self.connection.execute(
            "INSERT INTO source_files "
            "(source_file_id, path, sha256, size, ingested_at, run_id, source_year) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                self.source_file_id,
                metadata.source_file,
                metadata.source_sha256,
                metadata.size_bytes,
                ingested_at,
                self.run_id,
                self.source_year,
            ],
        )
        self.connection.execute(f"INSERT INTO despesas SELECT * FROM {self.table}")
        self.connection.execute("DELETE FROM deputados")
        self.connection.execute(
            "INSERT INTO deputados SELECT numero_deputado_id, nome_parlamentar, "
            "cpf, sigla_uf, sigla_partido, legislatura, codigo_legislatura, "
            "numero_carteira_parlamentar, cpf IS NULL AND sigla_uf = 'NA', "
            "source_file_id, source_record_number, id_documento, record_sha256, "
            "run_id, step_id FROM despesas_current "
            "QUALIFY row_number() OVER (PARTITION BY numero_deputado_id "
            "ORDER BY ano DESC, mes DESC, data_emissao DESC NULLS LAST, "
            "id_documento DESC, source_file_id DESC, source_record_number DESC) = 1"
        )
        self.connection.execute(f"DROP TABLE {self.table}")
        self.connection.execute("COMMIT")
        self.published = True
        self.closed = True


def _json_scalar(value: object) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unsupported expense scalar: {type(value).__name__}")
