"""SQL adapter and explicit transaction boundary. No frontend dependencies."""

from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import StaticPool

from .domain import Conflict, ValidationError


class Store:
    def __init__(self, url: str):
        if not url.startswith("sqlite:"):
            raise ValidationError("Only SQLite is supported by the initial adapter")
        self.engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if make_url(url).database in (None, "", ":memory:") else None,
        )
        self._lock = threading.RLock()
        self._current = contextvars.ContextVar(f"connection_{id(self)}", default=None)

        @event.listens_for(self.engine, "connect")
        def configure(dbapi, _record):
            dbapi.isolation_level = None
            cursor = dbapi.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        @event.listens_for(self.engine, "begin")
        def begin(connection):
            connection.exec_driver_sql(
                "BEGIN IMMEDIATE"
                if connection.get_execution_options().get("graph_write")
                else "BEGIN"
            )

        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.transaction(write=True) as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    @contextmanager
    def transaction(self, *, write: bool = False):
        with self._lock:
            current = self._current.get()
            try:
                if current is not None:
                    connection, writable = current
                    if write and not writable:
                        raise Conflict("Cannot upgrade a read snapshot to a write transaction")
                    if write:
                        with connection.begin_nested():
                            yield connection
                    else:
                        yield connection
                    return
                with self.engine.connect().execution_options(graph_write=write) as connection:
                    with connection.begin():
                        token = self._current.set((connection, write))
                        try:
                            yield connection
                        finally:
                            self._current.reset(token)
            except IntegrityError as exc:
                raise Conflict(
                    "Write violates uniqueness, references, or score constraints"
                ) from exc
            except OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    raise Conflict("Database is busy; retry the complete transaction") from exc
                raise

    @property
    def connection(self) -> Connection:
        current = self._current.get()
        if current is None:
            raise RuntimeError("A connection requires an active transaction")
        return current[0]

    def close(self):
        self.engine.dispose()
