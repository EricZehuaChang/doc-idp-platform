"""9.15 WP4: file document_meta/result_revision + transaction execution snapshot.

- files.document_meta (JSON nullable): classification metadata beside the
  user-facing result (§3.4/§3.5) — source_pages, doc_index, doc_type,
  category_id, handler, effective_schema, run metrics.
- files.result_revision (int, default 0): bumped on accepted review changes
  (artifact provenance in WP6).
- transactions.execution_snapshot (JSON nullable): immutable execution config
  at submit time (package + pinned dependency packages). NULL rows keep the
  legacy version-row read path.

Revision ID: f2a9c4e6b8d0
Revises: d4f6a8c2e0b1
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa

revision = "f2a9c4e6b8d0"
down_revision = "d4f6a8c2e0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 9.15 WP4 (§3.4): (skill_code, version) must be unique — duplicated version
    # numbers would make the submit-time snapshot and the editor's version rail
    # resolve to arbitrary rows. A pre-existing duplicate must be cleaned before
    # upgrading (the plan explicitly forbids blindly adding constraints).
    conn = op.get_bind()
    dupes = conn.execute(sa.text(
        "SELECT skill_code, tenant_id, COUNT(*) c FROM skill_versions "
        "GROUP BY skill_code, tenant_id, version HAVING c > 1")).fetchall()
    if dupes:
        raise RuntimeError(
            "skill_versions has duplicate (skill_code, version) rows — clean "
            f"them up before upgrading: {dupes[:5]}")
    op.create_index("ux_skill_versions_code_version", "skill_versions",
                    ["tenant_id", "skill_code", "version"], unique=True)
    with op.batch_alter_table("files") as b:
        b.add_column(sa.Column("document_meta", sa.JSON(), nullable=True))
        b.add_column(sa.Column("result_revision", sa.Integer(),
                               nullable=False, server_default="0"))
    with op.batch_alter_table("transactions") as b:
        b.add_column(sa.Column("execution_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_index("ux_skill_versions_code_version", table_name="skill_versions")
    with op.batch_alter_table("transactions") as b:
        b.drop_column("execution_snapshot")
    with op.batch_alter_table("files") as b:
        b.drop_column("result_revision")
        b.drop_column("document_meta")
