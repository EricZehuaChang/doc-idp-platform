"""Metadata-only detection/location API call history."""
from alembic import op
import sqlalchemy as sa
revision = "c2d4e6f8a902"
down_revision = "c1d3e5f7a901"
branch_labels = depends_on = None


def upgrade():
    op.create_table("api_call_log",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("api_key_id", sa.String(32), nullable=False, index=True),
        sa.Column("key_name", sa.String(100), nullable=False),
        sa.Column("owner_user_id", sa.String(32), nullable=True),
        sa.Column("endpoint", sa.String(40), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True))
    # PostgreSQL RLS is applied to every tenant table by db.apply_rls().


def downgrade():
    op.drop_table("api_call_log")
