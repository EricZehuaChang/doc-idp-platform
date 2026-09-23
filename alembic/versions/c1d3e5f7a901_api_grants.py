"""Personal API authorization ceiling; NULL preserves legacy grants."""
from alembic import op
import sqlalchemy as sa
revision = "c1d3e5f7a901"
down_revision = "b8d0f2a4c6e8"
branch_labels = depends_on = None


def upgrade():
    op.add_column("users", sa.Column("api_grants", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("users", "api_grants")
