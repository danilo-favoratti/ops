"""DuckDB run ledger, including transactional replay after initial migrations."""

from copy import deepcopy

from duckdb import DuckDBPyConnection

from opsiu.adapters.run_serialization import (
    EVENT_ADAPTER,
    RUN_ADAPTER,
    STEP_ADAPTER,
    encode_json,
    json_object,
)
from opsiu.application.ports import EventRecord, RunRecord, StepRecord

type LedgerRecord = RunRecord | StepRecord | EventRecord


class DuckDBLedger:
    def __init__(self, connection: DuckDBPyConnection | None = None) -> None:
        self.connection = connection
        self._pending: list[tuple[str, LedgerRecord]] = []
        self._owned_runs: set[str] = set()

    @staticmethod
    def schema_exists(connection: DuckDBPyConnection) -> bool:
        row = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'main' "
            "AND table_name IN ('runs', 'steps', 'events')"
        ).fetchone()
        return row is not None and row[0] == 3

    def attach(self, connection: DuckDBPyConnection) -> None:
        """Replay the buffered lifecycle atomically before enabling the live sink."""
        if self.connection is not None:
            if self.connection is connection:
                return
            raise RuntimeError("the DuckDB ledger is already attached")
        connection.execute("BEGIN TRANSACTION")
        try:
            for operation, record in self._pending:
                self._write(connection, operation, record)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        self.connection = connection
        self._pending.clear()

    def _dispatch(self, operation: str, record: LedgerRecord) -> None:
        if operation == "start_run":
            self._owned_runs.discard(record.run_id)
        elif record.run_id not in self._owned_runs:
            raise RuntimeError("this sink did not start an active run")
        if self.connection is None:
            self._pending.append((operation, deepcopy(record)))
        else:
            self._write(self.connection, operation, record)
        if operation == "start_run":
            self._owned_runs.add(record.run_id)
        elif operation == "finish_run":
            self._owned_runs.remove(record.run_id)

    @staticmethod
    def _write(
        connection: DuckDBPyConnection, operation: str, record: LedgerRecord
    ) -> None:
        if operation == "start_run" and isinstance(record, RunRecord):
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    record.run_id,
                    record.command,
                    encode_json(record.arguments),
                    record.metadata.settings_hash,
                    record.metadata.code_commit,
                    record.metadata.code_dirty,
                    record.started_at,
                    record.finished_at,
                    record.status,
                    encode_json(record.counters),
                ],
            )
        elif operation == "finish_run" and isinstance(record, RunRecord):
            connection.execute(
                "UPDATE runs SET finished_at = ?, status = ?, counters_json = ? "
                "WHERE run_id = ?",
                [
                    record.finished_at,
                    record.status,
                    encode_json(record.counters),
                    record.run_id,
                ],
            )
        elif operation == "start_step" and isinstance(record, StepRecord):
            connection.execute(
                "INSERT INTO steps VALUES (?, ?, ?, ?, ?, ?)",
                [
                    record.step_id,
                    record.run_id,
                    record.name,
                    record.started_at,
                    record.finished_at,
                    record.status,
                ],
            )
        elif operation == "finish_step" and isinstance(record, StepRecord):
            connection.execute(
                "UPDATE steps SET finished_at = ?, status = ? WHERE step_id = ?",
                [record.finished_at, record.status, record.step_id],
            )
        elif operation == "emit" and isinstance(record, EventRecord):
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                [
                    record.event_id,
                    record.run_id,
                    record.step_id,
                    record.kind,
                    encode_json(record.attributes),
                    record.created_at,
                ],
            )
        else:
            raise TypeError(f"invalid ledger operation: {operation}")

    def start_run(self, run: RunRecord) -> None:
        self._dispatch("start_run", run)

    def start_step(self, step: StepRecord) -> None:
        self._dispatch("start_step", step)

    def finish_step(self, step: StepRecord) -> None:
        self._dispatch("finish_step", step)

    def emit(self, event: EventRecord) -> None:
        self._dispatch("emit", event)

    def finish_run(self, run: RunRecord) -> None:
        self._dispatch("finish_run", run)

    def _connection(self) -> DuckDBPyConnection:
        if self.connection is None:
            raise RuntimeError("the DuckDB ledger is not attached")
        return self.connection

    def _runs(self, run_id: str | None = None) -> tuple[RunRecord, ...]:
        query = "SELECT * FROM runs"
        values: list[str] = []
        if run_id is not None:
            query += " WHERE run_id = ?"
            values.append(run_id)
        query += " ORDER BY started_at, run_id"
        cursor = self._connection().execute(query, values)
        names = [column[0] for column in cursor.description]
        result: list[RunRecord] = []
        for row in cursor.fetchall():
            data = dict(zip(names, row, strict=True))
            result.append(
                RUN_ADAPTER.validate_python(
                    {
                        "run_id": str(data["run_id"]),
                        "command": data["command"],
                        "arguments": json_object(data["args_json"]),
                        "metadata": {
                            "settings_hash": data["settings_hash"],
                            "code_commit": data["code_commit"],
                            "code_dirty": data["code_dirty"],
                            "python_version": "unknown",
                            "package_version": "unknown",
                        },
                        "started_at": data["started_at"],
                        "finished_at": data["finished_at"],
                        "status": data["status"],
                        "counters": json_object(data["counters_json"]),
                    }
                )
            )
        return tuple(result)

    def list_runs(self) -> tuple[RunRecord, ...]:
        return self._runs()

    def get_run(self, run_id: str) -> RunRecord | None:
        rows = self._runs(run_id)
        return rows[0] if rows else None

    def get_steps(self, run_id: str) -> tuple[StepRecord, ...]:
        cursor = self._connection().execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY started_at, step_id",
            [run_id],
        )
        names = [column[0] for column in cursor.description]
        result: list[StepRecord] = []
        for row in cursor.fetchall():
            data = dict(zip(names, row, strict=True))
            data.update(step_id=str(data["step_id"]), run_id=str(data["run_id"]))
            result.append(STEP_ADAPTER.validate_python(data))
        return tuple(result)

    def get_events(self, run_id: str) -> tuple[EventRecord, ...]:
        cursor = self._connection().execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY created_at, event_id",
            [run_id],
        )
        names = [column[0] for column in cursor.description]
        result: list[EventRecord] = []
        for row in cursor.fetchall():
            data = dict(zip(names, row, strict=True))
            data.update(
                event_id=str(data["event_id"]),
                run_id=str(data["run_id"]),
                step_id=str(data["step_id"]) if data["step_id"] is not None else None,
                attributes=json_object(data.pop("attributes_json")),
            )
            result.append(EVENT_ADAPTER.validate_python(data))
        return tuple(result)
