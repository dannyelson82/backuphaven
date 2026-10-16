"""Engine, sessions and migrations."""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def alembic_config(engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["engine"] = engine
    return config


class Database:
    """Owns the engine. Reads run concurrently; writes take one process-wide lock."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = make_engine(f"sqlite:///{path}")
        self._factory = sessionmaker(self.engine, expire_on_commit=False)
        self._write_lock = threading.Lock()

    def migrate(self) -> None:
        """Bring the schema up to date. Safe to run on every start."""
        with self._write_lock:
            command.upgrade(alembic_config(self.engine), "head")

    @contextmanager
    def read(self) -> Iterator[Session]:
        with self._factory() as session:
            yield session

    @contextmanager
    def write(self) -> Iterator[Session]:
        """A transaction that commits on success and rolls back on error."""
        with self._write_lock, self._factory() as session, session.begin():
            yield session

    def on_change(self, model: type, callback: Callable[[], None]) -> None:
        """Call ``callback`` after every commit that added, changed or deleted a ``model`` row.

        It runs on the committing thread while the write lock is still held,
        so it must be quick and must not touch the database itself.
        """
        flag = f"changed:{model.__name__}"

        def after_flush(session: Session, _context: Any) -> None:
            if any(isinstance(o, model) for o in (*session.new, *session.dirty, *session.deleted)):
                session.info[flag] = True

        def after_commit(session: Session) -> None:
            if session.info.pop(flag, False):
                callback()

        def after_rollback(session: Session) -> None:
            session.info.pop(flag, None)

        event.listen(self._factory, "after_flush", after_flush)
        event.listen(self._factory, "after_commit", after_commit)
        event.listen(self._factory, "after_rollback", after_rollback)

    def close(self) -> None:
        self.engine.dispose()
