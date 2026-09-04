"""Built-in skill templates (onboarding P0): the starter gallery and its
one-click import.

The load-bearing case is `test_two_tenants_can_import_the_same_template`:
`Skill.code` is a global primary key, so a template that handed out its own id
as the skill code would 409 for every tenant after the first — the gallery
would work in dev and break on the second customer.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.skillengine import catalog


# —— unit: the shipped templates themselves ——

def test_every_shipped_template_parses_and_has_fields():
    """A template that fails to parse is silently dropped from the gallery, so
    assert on the files directly — otherwise shipping a broken one is invisible."""
    ids = [p.stem for p in catalog._paths()]
    assert ids, "no templates shipped"
    for tid in ids:
        pkg = catalog.get_template(tid)
        assert pkg is not None, f"{tid} failed to parse"
        assert pkg.name and pkg.fields, f"{tid} has no name/fields"
        assert pkg.skill_code, f"{tid} has no skill_code"


def test_invoice_template_kept_its_tables_and_validators():
    """Guards the derivation from the Insavlo XCMG config: the line-item table
    and the total = net + tax arithmetic check are the parts worth having."""
    pkg = catalog.get_template("invoice_general")
    assert pkg is not None
    tables = [f for f in pkg.fields if f.type == "table"]
    assert len(tables) == 1 and len(tables[0].columns) == 9
    kinds = {v.type for v in pkg.validators}
    assert "sum_equals" in kinds
    # the cross-check field is an LLM conclusion, not a verbatim lift
    assert any(f.mode == "inferred" for f in pkg.fields)


def test_get_template_rejects_path_traversal_and_unknown_ids():
    for bad in ("../configs/providers", "a/b", "a\\b", ".hidden", "", "x" * 80):
        assert catalog.get_template(bad) is None
    assert catalog.get_template("no_such_template") is None


def test_candidate_codes_are_per_tenant_and_deduplicate():
    a = list(zip(range(3), catalog.candidate_codes("invoice_general", "tenant-a")))
    b = list(zip(range(3), catalog.candidate_codes("invoice_general", "tenant-b")))
    a_codes, b_codes = [c for _, c in a], [c for _, c in b]
    assert a_codes[0] != b_codes[0], "two tenants must not derive the same code"
    assert len(set(a_codes)) == 3, "repeat imports must get distinct codes"
    assert all(c.startswith("invoice_general_") for c in a_codes)
    assert all(len(c) <= 64 for c in a_codes), "Skill.code is String(64)"


# —— API level ——

@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            yield c


async def test_gallery_lists_cards_with_counts(client):
    r = await client.get("/api/v1/skills/templates")
    assert r.status_code == 200
    cards = r.json()["templates"]
    assert cards
    inv = next(c for c in cards if c["id"] == "invoice_general")
    # the card must say enough to choose without opening the editor
    assert inv["name"] and inv["description"]
    assert inv["field_count"] > 0 and inv["table_count"] == 1
    assert inv["column_count"] == 9 and inv["validator_count"] == 3


async def test_templates_route_is_not_read_as_a_skill_code(client):
    """GET /skills/{skill_code} is declared after this one; if the order ever
    flips, the gallery 404s as a missing skill instead of listing."""
    r = await client.get("/api/v1/skills/templates")
    assert r.status_code == 200 and "templates" in r.json()


async def test_import_creates_a_draft_skill(client):
    r = await client.post("/api/v1/skills/templates/invoice_general/import")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 1 and body["status"] == "draft"
    assert body["skill_code"].startswith("invoice_general_")
    assert body["name"] == "通用发票"

    listed = (await client.get("/api/v1/skills")).json()
    rows = listed["skills"] if isinstance(listed, dict) else listed
    assert any(s["skill_code"] == body["skill_code"] for s in rows)

    detail = await client.get(f"/api/v1/skills/{body['skill_code']}")
    assert detail.status_code == 200
    # the package landed intact, under the tenant-scoped code
    pkg = detail.json()["latest_package"]
    assert pkg["skill_code"] == body["skill_code"]
    assert len(pkg["fields"]) == len(catalog.get_template("invoice_general").fields)


async def test_two_tenants_can_import_the_same_template(client):
    """Skill.code is a global PK — the second tenant must still succeed, and
    must not see the first tenant's skill."""
    a = await client.post("/api/v1/skills/templates/invoice_general/import",
                          headers={"X-Tenant-Id": "tenant-a"})
    b = await client.post("/api/v1/skills/templates/invoice_general/import",
                          headers={"X-Tenant-Id": "tenant-b"})
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    code_a, code_b = a.json()["skill_code"], b.json()["skill_code"]
    assert code_a != code_b

    b_list = (await client.get("/api/v1/skills",
                               headers={"X-Tenant-Id": "tenant-b"})).json()
    b_rows = b_list["skills"] if isinstance(b_list, dict) else b_list
    b_codes = {s["skill_code"] for s in b_rows}
    assert code_b in b_codes and code_a not in b_codes


async def test_same_tenant_importing_twice_gets_two_skills(client):
    first = await client.post("/api/v1/skills/templates/purchase_order/import")
    second = await client.post("/api/v1/skills/templates/purchase_order/import")
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["skill_code"] != second.json()["skill_code"]


async def test_unknown_template_is_404(client):
    r = await client.post("/api/v1/skills/templates/nope/import")
    assert r.status_code == 404
