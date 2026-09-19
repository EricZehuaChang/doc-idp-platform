"""发起人列筛选（2026-09-19 需求：任务清单的发起人列支持筛选）.

Two surfaces:
- `GET /api/v1/files/initiators` — the选项 list (value/label/type/count), built
  from the same population the ledger shows;
- `GET /api/v1/files?initiator=…` — the filter itself, matching the values that
  endpoint hands out (`user:<label>`, `api_key:<label>`, `unknown`, `anonymous`).

Rows are inserted directly: this is about the task-ledger read surface, not
about how submissions are made (test_initiator covers that).
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import security


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IDP_AUTH_MODE", "on")
    monkeypatch.setenv("IDP_ADMIN_PASSWORD", "admin-pass-123")
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    security._secret = None
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.post("/api/v1/auth/login",
                             json={"email": "admin@example.com",
                                   "password": "admin-pass-123"})
            c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
            yield c


async def _seed() -> None:
    """One ledger row per initiator flavour, plus two rows that must NOT show up
    in the options (a deleted task and a Playground test run)."""
    from app.db import session_factory
    from app.models import FileRecord, Transaction

    rows = [
        # (skill, purpose, status, type, label, file_name)
        ("s", "production", "completed", "user", "alice@example.com", "a.pdf"),
        ("s", "production", "completed", "user", "alice@example.com", "a2.pdf"),
        ("s", "production", "completed", "user", "bob@example.com", "b.pdf"),
        ("s", "production", "completed", "api_key", "ERP 集成", "c.pdf"),
        ("s", "production", "completed", "anonymous", None, "d.pdf"),
        ("s", "production", "completed", None, None, "legacy.pdf"),
        ("s", "production", "completed", "unknown", "历史任务", "legacy2.pdf"),
        ("s", "production", "completed", "user", "gone@example.com", "del.pdf"),
        ("s", "test", "completed", "user", "tester@example.com", "test.pdf"),
    ]
    sf = session_factory()
    async with sf() as s:
        for i, (skill, purpose, status, itype, label, name) in enumerate(rows):
            txn = Transaction(id=f"t{i:031d}", tenant_id="default", skill_code=skill,
                              skill_version=1, status=status, purpose=purpose,
                              initiator_type=itype, initiator_label=label)
            s.add(txn)
            await s.flush()
            s.add(FileRecord(id=f"f{i:031d}", tenant_id="default",
                             transaction_id=txn.id, file_name=name,
                             storage_path=f"files/default/{txn.id}/{name}",
                             status="completed", page_count=1))
        await s.flush()
        # the deleted one: tombstoned transaction (same shape the delete route leaves)
        await s.execute(
            Transaction.__table__.update().where(Transaction.id == "t0000000000000000000000000000007")
            .values(status="deleted"))
        await s.commit()


async def _ledger(c, **params) -> dict:
    # httpx encodes the params: 中文 labels (api_key:ERP 集成) must not go raw
    r = await c.get("/api/v1/files", params={"page": 1, "page_size": 50, **params})
    assert r.status_code == 200, r.text
    return r.json()


async def test_initiator_options_cover_every_flavour(client):
    await _seed()
    r = await client.get("/api/v1/files/initiators")
    assert r.status_code == 200, r.text
    opts = r.json()["initiators"]
    by_value = {o["value"]: o for o in opts}

    assert by_value["user:alice@example.com"]["count"] == 2
    assert by_value["user:alice@example.com"]["label"] == "alice@example.com"
    assert by_value["user:alice@example.com"]["type"] == "user"
    assert by_value["user:bob@example.com"]["count"] == 1
    assert by_value["api_key:ERP 集成"]["type"] == "api_key"
    # 免登录 / 历史任务 are single buckets with the ledger's wording
    assert by_value["anonymous"]["label"] == "免登录"
    assert by_value["unknown"]["label"] == "历史任务"
    assert by_value["unknown"]["count"] == 2      # NULL type + explicit unknown

    # the deleted task and the Playground run never become options — otherwise a
    # choice could sit in the menu and always return zero rows
    assert "user:gone@example.com" not in by_value
    assert "user:tester@example.com" not in by_value
    # unknown first (it is usually the biggest bucket), then named users
    assert opts[0]["type"] == "unknown"
    assert [o["type"] for o in opts].count("user") == 2


@pytest.mark.parametrize("value,expected", [
    ("user:alice@example.com", 2),
    ("user:bob@example.com", 1),
    ("api_key:ERP 集成", 1),
    ("anonymous", 1),
    ("unknown", 2),
    ("user", 3),                    # whole type: SQL-level match — alice×2 + bob
                                    # + the Playground submitter the ledger hides;
                                    # a bare type filter is coarser than the
                                    # options list on purpose
    ("nobody@example.com", 0),      # matches nothing, never 500
    ("user:nobody@example.com", 0),
])
async def test_initiator_filter(client, value, expected):
    await _seed()
    page = await _ledger(client, initiator=value)
    assert page["total"] == expected, (value, page["total"])


async def test_initiator_filter_composes_with_other_columns(client):
    await _seed()
    # 发起人 × 状态: alice's two rows are completed, so passed narrows to zero
    assert (await _ledger(client, initiator="user:alice@example.com",
                          status="completed"))["total"] == 2
    assert (await _ledger(client, initiator="user:alice@example.com",
                          status="passed"))["total"] == 0
    # 发起人 × 技能: a skill nobody submitted under -> zero, not an error
    assert (await _ledger(client, initiator="unknown", skill_code="other"))["total"] == 0
    # the row payload still carries what the column renders
    row = (await _ledger(client, initiator="api_key:ERP 集成"))["data"][0]
    assert row["initiator_type"] == "api_key"
    assert row["initiator_label"] == "ERP 集成"


async def test_initiator_filter_is_tenant_scoped(client):
    """A label from another tenant must never match, even when the value came
    from this tenant's own options list."""
    await _seed()
    from app.db import session_factory
    from app.models import FileRecord, Transaction
    async with session_factory()() as s:
        txn = Transaction(id="acme" + "0" * 28, tenant_id="acme", skill_code="s",
                          skill_version=1, status="completed", purpose="production",
                          initiator_type="user", initiator_label="alice@example.com")
        s.add(txn)
        await s.flush()
        s.add(FileRecord(id="acmef" + "0" * 27, tenant_id="acme",
                         transaction_id=txn.id, file_name="acme.pdf",
                         storage_path="files/acme/acme.pdf", status="completed"))
        await s.commit()

    assert (await _ledger(client, initiator="user:alice@example.com"))["total"] == 2
    opts = (await client.get("/api/v1/files/initiators")).json()["initiators"]
    assert [o["count"] for o in opts if o["value"] == "user:alice@example.com"] == [2]
