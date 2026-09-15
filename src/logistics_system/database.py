from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from .config import DATABASE_PATH
from .generator import DemoBuildResult, build_demo_database


def ensure_demo_database(database_path: Path | str = DATABASE_PATH) -> Path:
    path = Path(database_path)
    if not path.exists():
        build_demo_database(path)
    return path


@contextmanager
def connect_read_only(database_path: Path | str = DATABASE_PATH) -> Iterator[duckdb.DuckDBPyConnection]:
    path = ensure_demo_database(database_path)
    connection = duckdb.connect(str(path), read_only=True)
    try:
        yield connection
    finally:
        connection.close()


__all__ = ["DemoBuildResult", "build_demo_database", "connect_read_only", "ensure_demo_database"]

