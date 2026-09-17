"""skills.updated_at for the roster's recent-update sort (9.15 R02)

Revision ID: b3d1f8a2c4e5
Revises: 8c201dddb7dc
Create Date: 2026-09-17

Backfill = max(skill.created_at, max(version.created_at)) per skill — the only
honest estimate of "last edited" for pre-existing rows; nothing is invented.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3d1f8a2c4e5'
down_revision: Union[str, None] = '8c201dddb7dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('skills', schema=None) as batch_op:
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True),
                                      nullable=False,
                                      server_default=sa.text('CURRENT_TIMESTAMP')))
    # honest backfill for every pre-existing row: newest version edit if any,
    # else the skill's birthday (the server_default above is just NOT NULL filler)
    op.execute(
        "UPDATE skills SET updated_at = COALESCE("
        " (SELECT MAX(sv.created_at) FROM skill_versions sv WHERE sv.skill_code = skills.code),"
        " skills.created_at)")


def downgrade() -> None:
    with op.batch_alter_table('skills', schema=None) as batch_op:
        batch_op.drop_column('updated_at')
