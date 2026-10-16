"""Alembic environment. Runs against the engine passed in by ``Database.migrate``."""

from alembic import context
from sqlalchemy.engine import Engine

from backuphaven.db.models import Base

engine: Engine = context.config.attributes["engine"]

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,  # SQLite needs table rebuilds for most ALTERs
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
