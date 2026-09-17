"""transactions initiator snapshot + submit idempotency; ApiKey kinds (9.15 WP2)

Revision ID: c9d2e4f6a8b0
Revises: b3d1f8a2c4e5
Create Date: 2026-09-17

Legacy transaction rows backfill to initiator_type=unknown /
initiator_label=历史任务 (verified_by is deliberately NOT a source).
Legacy api_keys backfill to key_type=application — their behaviour is
unchanged (mask-guard depends on it). The idempotency unique index is
PARTIAL (WHERE idempotency_key IS NOT NULL): pre-existing rows all have
NULL there, so the constraint can never fail on upgrade — but the table
is still checked for duplicates defensively before creating it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d2e4f6a8b0'
down_revision: Union[str, None] = 'b3d1f8a2c4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('initiator_type', sa.String(16), nullable=True))
        batch_op.add_column(sa.Column('initiator_id', sa.String(128), nullable=True))
        batch_op.add_column(sa.Column('initiator_label', sa.String(320), nullable=True))
        batch_op.add_column(sa.Column('initiator_user_id', sa.String(32), nullable=True))
        batch_op.add_column(sa.Column('api_key_id', sa.String(32), nullable=True))
        batch_op.add_column(sa.Column('idempotency_key', sa.String(128), nullable=True))
        batch_op.add_column(sa.Column('idem_principal', sa.String(160), nullable=True))
        batch_op.add_column(sa.Column('request_fingerprint', sa.String(64), nullable=True))
    op.execute("UPDATE transactions SET initiator_type='unknown', "
               "initiator_label='历史任务' WHERE initiator_type IS NULL")

    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.add_column(sa.Column('key_type', sa.String(16), nullable=False,
                                      server_default='application'))
        batch_op.add_column(sa.Column('owner_user_id', sa.String(32), nullable=True))
        batch_op.add_column(sa.Column('allowed_skill_codes', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('last_used_at', sa.DateTime(timezone=True),
                                      nullable=True))

    # submit idempotency: one in-flight key per (tenant, principal). Partial
    # index keeps every legacy row (NULL key) out of the constraint.
    conn = op.get_bind()
    dup = conn.execute(sa.text(
        "SELECT COUNT(*) FROM (SELECT tenant_id, idem_principal, idempotency_key "
        "FROM transactions WHERE idempotency_key IS NOT NULL "
        "GROUP BY tenant_id, idem_principal, idempotency_key "
        "HAVING COUNT(*) > 1)")).scalar()
    if dup:
        raise RuntimeError(
            f"transactions has {dup} duplicate idempotency groups; resolve before upgrading")
    op.create_index(
        'ux_transactions_idem', 'transactions',
        ['tenant_id', 'idem_principal', 'idempotency_key'], unique=True,
        sqlite_where=sa.text('idempotency_key IS NOT NULL'),
        postgresql_where=sa.text('idempotency_key IS NOT NULL'))


def downgrade() -> None:
    op.drop_index('ux_transactions_idem', table_name='transactions')
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_column('last_used_at')
        batch_op.drop_column('allowed_skill_codes')
        batch_op.drop_column('owner_user_id')
        batch_op.drop_column('key_type')
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.drop_column('request_fingerprint')
        batch_op.drop_column('idem_principal')
        batch_op.drop_column('idempotency_key')
        batch_op.drop_column('api_key_id')
        batch_op.drop_column('initiator_user_id')
        batch_op.drop_column('initiator_label')
        batch_op.drop_column('initiator_id')
        batch_op.drop_column('initiator_type')
