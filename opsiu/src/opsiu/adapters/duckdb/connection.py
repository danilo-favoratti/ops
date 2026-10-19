"""Explicitly scoped connections to a configured DuckDB database."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb


@contextmanager
def connect_database(
    db_path: Path, *, read_only: bool = False
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a database, creating parent directories only for writable access."""
    if not read_only:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(db_path), read_only=read_only)
    try:
        yield connection
    finally:
        connection.close()
