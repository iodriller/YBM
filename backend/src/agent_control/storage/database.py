from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from collections.abc import Iterator

from agent_control.storage.migrations import SCHEMA_STATEMENTS


class Database:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.path = self._sqlite_path(database_url)

    @staticmethod
    def _sqlite_path(database_url: str) -> str:
        if database_url == "sqlite:///:memory:":
            return ":memory:"
        if database_url.startswith("sqlite:///"):
            return database_url.removeprefix("sqlite:///")
        raise ValueError("only sqlite:/// database URLs are supported in the MVP")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
