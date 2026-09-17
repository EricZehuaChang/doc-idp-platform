"""9.15 WP6: file_artifacts (R13 output files).

Unique (file_id, source_revision, config_hash, doc_index) — reruns are
idempotent and a fresh review revision replaces the older artifact rows.

Revision ID: b8d0f2a4c6e8
Revises: a6c8e0f2d4b6
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa

revision = "b8d0f2a4c6e8"
down_revision = "a6c8e0f2d4b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "file_artifacts",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("file_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("doc_index", sa.Integer(), nullable=True),
        sa.Column("source_revision", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("config_hash", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("action", sa.String(length=16), nullable=False,
                  server_default="rename"),
        sa.Column("display_name", sa.String(length=500), nullable=False,
                  server_default=""),
        sa.Column("storage_key", sa.String(length=1000), nullable=False,
                  server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False,
                  server_default=""),
        # sa.false() renders per dialect (SQLite "0", PostgreSQL "false"):
        # a literal 0 is an integer expression and PostgreSQL rejects it for a
        # boolean column (found by CI's PG matrix, 2026-09-18)
        sa.Column("searchable", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
    )
    op.create_index("ux_file_artifacts_rev", "file_artifacts",
                    ["file_id", "source_revision", "config_hash", "doc_index"],
                    unique=True)


def downgrade() -> None:
    op.drop_index("ux_file_artifacts_rev", table_name="file_artifacts")
    op.drop_table("file_artifacts")
