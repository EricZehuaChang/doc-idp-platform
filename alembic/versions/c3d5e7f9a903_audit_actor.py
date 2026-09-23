"""Allow full email addresses in the audit actor."""
from alembic import op
import sqlalchemy as sa
revision = "c3d5e7f9a903"
down_revision = "c2d4e6f8a902"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("audit_log") as batch:
        batch.alter_column("actor", existing_type=sa.String(64), type_=sa.String(320), existing_nullable=False)


def downgrade():
    # Restore a pre-upgrade DB backup when reverting production. Refuse to
    # truncate audit identities on PostgreSQL (SQLite doesn't enforce width).
    with op.batch_alter_table("audit_log") as batch:
        batch.alter_column("actor", existing_type=sa.String(320), type_=sa.String(64), existing_nullable=False)
