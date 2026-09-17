"""9.15 WP5: transaction purpose + studio_runs (Playground).

- transactions.purpose (production|test, default production): test runs are
  excluded from /files, stats, cabinet, review queue, webhooks and agent scope
  (§3.9). Existing rows backfill to production — they were all real work.
- studio_runs: one Playground execution per sample; the transaction row keeps
  the frozen click-time snapshot.

Revision ID: a6c8e0f2d4b6
Revises: f2a9c4e6b8d0
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa

revision = "a6c8e0f2d4b6"
down_revision = "f2a9c4e6b8d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("files") as b:
        b.add_column(sa.Column("images_path", sa.String(length=255), nullable=True))
    with op.batch_alter_table("transactions") as b:
        b.add_column(sa.Column("purpose", sa.String(length=16), nullable=False,
                               server_default="production"))
    op.execute("UPDATE transactions SET purpose = 'production'")
    op.create_table(
        "studio_runs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("skill_code", sa.String(length=64), nullable=False, index=True),
        sa.Column("skill_version", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("transaction_id", sa.String(length=32), nullable=False),
        sa.Column("file_id", sa.String(length=32), nullable=True),
        sa.Column("package_hash", sa.String(length=64), nullable=False,
                  server_default=""),
        sa.Column("created_by", sa.String(length=200), nullable=False,
                  server_default=""),
        sa.Column("status", sa.String(length=24), nullable=False,
                  server_default="queued"),
        sa.Column("processing_mode", sa.String(length=16), nullable=False,
                  server_default="balanced"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
    )
    op.create_index("ix_studio_runs_transaction_id", "studio_runs",
                    ["transaction_id"])


def downgrade() -> None:
    with op.batch_alter_table("files") as b:
        b.drop_column("images_path")
    op.drop_index("ix_studio_runs_transaction_id", table_name="studio_runs")
    op.drop_table("studio_runs")
    with op.batch_alter_table("transactions") as b:
        b.drop_column("purpose")
