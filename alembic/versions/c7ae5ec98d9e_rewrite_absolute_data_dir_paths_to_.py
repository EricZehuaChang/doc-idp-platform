"""rewrite absolute data_dir paths to storage keys

Revision ID: c7ae5ec98d9e
Revises: c8c989251f69
Create Date: 2026-09-08 00:12:43.706532

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7ae5ec98d9e'
down_revision: Union[str, None] = 'c8c989251f69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# WP2 (docs/ARCHITECTURE.md §3): rows written before the storage seam hold
# absolute paths under the runtime data_dir; new rows hold relative keys.
# This backfill strips the prefix and normalizes separators to '/'. It is
# idempotent (key-form rows don't match the prefix) and optional in practice:
# databases adopted via `alembic stamp head` skip it, and LocalStorage still
# reads legacy absolute paths — the rewrite is hygiene, not a dependency.
_TABLES = (("files", ("storage_path", "udr_path")),
           ("golden_samples", ("storage_path",)))


def _root() -> str:
    from app.config import get_settings
    root = str(get_settings().data_dir)
    return root if root.endswith(("/", "\\")) else root + "/"


def upgrade() -> None:
    root = _root()
    bind = op.get_bind()
    for table, columns in _TABLES:
        for col in columns:
            bind.execute(sa.text(
                f"UPDATE {table} SET {col} = replace(substr({col}, :start), '\\', '/') "
                f"WHERE {col} IS NOT NULL AND substr({col}, 1, :n) = :root"
            ), {"start": len(root) + 1, "n": len(root), "root": root})


def downgrade() -> None:
    """Re-prefix keys with the runtime data_dir. Absolute paths of any other
    shape (posix '/', Windows drive '_:') are left untouched."""
    import os
    from app.config import get_settings
    root = str(get_settings().data_dir).rstrip("/\\")
    bind = op.get_bind()
    sep = "\\" if os.name == "nt" else "/"
    for table, columns in _TABLES:
        for col in columns:
            bind.execute(sa.text(
                f"UPDATE {table} SET {col} = :root || :sep || replace({col}, '/', :sep) "
                f"WHERE {col} IS NOT NULL AND {col} NOT LIKE '/%' AND {col} NOT LIKE '_:%'"
            ), {"root": root, "sep": sep})
