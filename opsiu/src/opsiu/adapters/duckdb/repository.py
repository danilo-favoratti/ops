"""DuckDB persistence implementations for application repository ports."""

from datetime import date
from decimal import Decimal

from duckdb import DuckDBPyConnection

from opsiu.domain.errors import DomainError
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
