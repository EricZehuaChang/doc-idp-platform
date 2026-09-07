"""Domain model — M1 lays the full data foundation on day one (design v0.2 §10):
every business table carries tenant_id (§11.1); verification carries assignee
(who should review) separate from locked_by (who is reviewing now); corrections
and golden_samples accumulate from the first task (quality dashboard is just a
query later); credit ledger runs in shadow-billing mode (§12.6).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)   # slug, e.g. "default"
    name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|suspended|closed (§11.6)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(32), default="operator")  # RBAC minimal set (§11.10)
    # local-login credential; NULL for SSO-only or not-yet-activated accounts (§11.9)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # disable takes effect on next request
    # account lifecycle (§11.8): invited users verify via email token; admin-created
    # accounts are verified by fiat but must change password on first login
    email_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    # SSO fields reserved on day one to avoid rework (§11.9 M1 note)
    auth_provider: Mapped[str] = mapped_column(String(32), default="local")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unlimited: Mapped[bool] = mapped_column(Boolean, default=False)  # Owner Root flag (§12.7)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)   # store hash, never the key
    # first 8 chars of the random part — the only identifying glimpse admins get
    # in the list UI (KBase pattern: full key is shown exactly once at creation)
    prefix: Mapped[str] = mapped_column(String(16), default="")
    name: Mapped[str] = mapped_column(String(100), default="")
    scopes: Mapped[str] = mapped_column(String(255), default="process:write,skills:read")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    quota_mode: Mapped[str] = mapped_column(String(16), default="pool")  # pool|allocated (§12.7)
    # allocated mode: budget carved out of the tenant paid pool (key_transfer
    # ledger rows); this key 402s on its own without touching sibling keys
    allocated_balance: Mapped[float] = mapped_column(Float, default=0.0)
    allocated_frozen: Mapped[float] = mapped_column(Float, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuthToken(Base):
    """One-time account tokens (§11.8): invite / password reset / email verify.
    DB-backed (not stateless signatures) so consumption is single-use by
    construction; only the sha256 of the token ever touches the DB."""
    __tablename__ = "auth_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(16))       # invite | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Skill(Base):
    """Skill identity; content lives in versioned SkillVersion rows (§5.1:
    published versions are immutable — running tasks pin a version)."""
    __tablename__ = "skills"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16), default="extract")  # extract|audit (§5.5)
    state: Mapped[str] = mapped_column(String(16), default="active")  # active|disabled|deleted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    skill_code: Mapped[str] = mapped_column(ForeignKey("skills.code"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|published|archived
    package: Mapped[dict] = mapped_column(JSON)          # compiled SkillPackage (§5.1)
    changelog: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    skill_code: Mapped[str] = mapped_column(String(64), index=True)
    skill_version: Mapped[int] = mapped_column(Integer, default=0)   # pinned at submit time
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class FileRecord(Base):
    """Per-file state machine row (§7): queued -> processing ->
    pending_verification|completed -> passed/rejected -> exported. error anywhere."""
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    # multi-doc split (M2): children point at the bundle they came from;
    # the parent ends in status "split" and is never extracted itself
    parent_file_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    file_name: Mapped[str] = mapped_column(String(500))
    # storage key under the configured backend ('files/...'), or a legacy
    # absolute path from before the WP2 storage seam (read-compatible)
    storage_path: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    # result: {field: {"$value","$confidence"(0-3),"$bbox","$pages","inferred"?,"$reasoning"?}}
    # tables are lists of row objects — same container, live-verified schema (sources/2026-07-14)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    udr_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # UDR json storage key (or legacy absolute path)
    # when the processing pipeline finished with this file (result written or
    # failed): the per-document processing-speed figure on the task ledger.
    # updated_at can't serve (review locks/corrections bump it too).
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)    # token-level cost metering
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # review workflow: assignee = who SHOULD review; locked_by = who IS reviewing (PM item #2)
    assignee: Mapped[str | None] = mapped_column(String(32), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleanup_status: Mapped[str] = mapped_column(String(16), default="keep")  # data lifecycle (§4.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Correction(Base):
    """Human correction log — the accuracy-proxy data behind the skill quality
    dashboard (PM item #1). Written on every field edit during verification."""
    __tablename__ = "corrections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    file_id: Mapped[str] = mapped_column(String(32), index=True)
    skill_code: Mapped[str] = mapped_column(String(64), index=True)
    skill_version: Mapped[int] = mapped_column(Integer, default=0)
    field: Mapped[str] = mapped_column(String(200))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GoldenSample(Base):
    """Fixed regression set per skill (PM item #4): publish-time diff runs
    new vs old version over these samples."""
    __tablename__ = "golden_samples"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    skill_code: Mapped[str] = mapped_column(String(64), index=True)
    # storage key ('golden/...') or a legacy absolute path (read-compatible)
    storage_path: Mapped[str] = mapped_column(String(1000))
    expected: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CreditAccount(Base):
    __tablename__ = "credit_accounts"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paid_balance: Mapped[float] = mapped_column(Float, default=0.0)   # dual bucket (§12.7)
    gift_balance: Mapped[float] = mapped_column(Float, default=0.0)
    frozen: Mapped[float] = mapped_column(Float, default=0.0)


class CreditLedger(Base):
    """Append-only money trail (§12.2). M1 runs shadow billing: entries are
    recorded (kind=shadow_meter) but nothing is charged — pricing gets three
    months of real data before it goes live."""
    __tablename__ = "credit_ledger"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(24))   # shadow_meter|topup|freeze|charge|unfreeze|gift|adjust
    amount: Mapped[float] = mapped_column(Float)
    bucket: Mapped[str] = mapped_column(String(8), default="paid")   # paid|gift
    transaction_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    note: Mapped[str] = mapped_column(String(500), default="")
    balance_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GiftRequest(Base):
    """Marketing credit grants (§12.7): every grant attempt is a request row —
    small ones auto-approve on the spot, ones above the review threshold wait
    for a SECOND admin (dual control). tenant_id is the requesting platform
    tenant (RLS keeps the approval queue on the platform side);
    target_tenant_id is the beneficiary account."""
    __tablename__ = "gift_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    target_tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[float] = mapped_column(Float)
    campaign: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(500))
    requested_by: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|approved|rejected
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"    # config layering (§11.10): platform tier
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class TenantSetting(Base):
    __tablename__ = "tenant_settings"      # config layering (§11.10): tenant tier
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class AuditLog(Base):
    __tablename__ = "audit_log"            # append-only; monthly partition on PG (§9.1)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
