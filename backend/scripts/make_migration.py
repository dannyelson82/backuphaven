"""Generate a new Alembic migration from model changes.

Usage: uv run python scripts/make_migration.py "short description"
Review the generated file before committing it.
"""

import sys
import tempfile
from pathlib import Path

from alembic import command

from backuphaven.db.engine import Database, alembic_config


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "scratch.db")
        db.migrate()  # existing migrations first, then diff against the models
        command.revision(alembic_config(db.engine), message=sys.argv[1], autogenerate=True)
        db.close()


if __name__ == "__main__":
    main()
