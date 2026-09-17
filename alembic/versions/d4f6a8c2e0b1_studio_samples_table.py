"""editor studio samples (9.15 WP3)

Revision ID: d4f6a8c2e0b1
Revises: c9d2e4f6a8b0
Create Date: 2026-09-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4f6a8c2e0b1'
down_revision: Union[str, None] = 'c9d2e4f6a8b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'studio_samples',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('skill_code', sa.String(length=64), nullable=True),
        sa.Column('uploader_id', sa.String(length=32), nullable=False),
        sa.Column('file_name', sa.String(length=500), nullable=False),
        sa.Column('storage_key', sa.String(length=1000), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('studio_samples', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_studio_samples_tenant_id'),
                              ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_studio_samples_skill_code'),
                              ['skill_code'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('studio_samples', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_studio_samples_skill_code'))
        batch_op.drop_index(batch_op.f('ix_studio_samples_tenant_id'))
    op.drop_table('studio_samples')
