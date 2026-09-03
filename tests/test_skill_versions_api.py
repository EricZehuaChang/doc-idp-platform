"""Skill version semantics + the task-ledger filters (2026-08-26 fix round).

Two behaviours are pinned here because both were customer-visible bugs:
- P04/P05: 「保存」 must update the draft in place. The editor used to call the
  create-draft route on every save, so each save minted vN+1, the version list
  filled with noise, and the version note the user had just typed appeared to
  vanish (it had landed on a version they were no longer looking at).
- P09: task-list narrowing happens in SQL, including the total, so the pager
  cannot disagree with the rows on screen.
"""
from httpx import ASGITransport, AsyncClient

from app.skillengine.schema import FieldSpec, SkillPackage


def _pkg(**kw) -> dict:
    return SkillPackage(skill_code="verspec", name="版本语义",
                        fields=[FieldSpec(name="invoice_no")], **kw).model_dump()


async def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.main import create_app
    return create_app()


async def test_save_updates_draft_in_place_and_keeps_the_version_note(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.post("/api/v1/skills",
                             json={"package": _pkg(), "changelog": "第一版"})
            assert r.status_code == 201

            # three saves onto the v1 draft must not create v2/v3/v4
            for i in range(3):
                r = await c.put("/api/v1/skills/verspec/versions/1",
                                json={"package": _pkg(), "changelog": f"第一版（改{i}）"})
                assert r.status_code == 200, r.text
                assert r.json() == {"skill_code": "verspec", "version": 1,
                                    "status": "draft"}
            d = (await c.get("/api/v1/skills/verspec")).json()
            assert [v["version"] for v in d["versions"]] == [1]
            # the note the user typed is on the version they were editing
            assert d["versions"][0]["changelog"] == "第一版（改2）"

            # an empty changelog is "not edited in this save", not "blank it"
            await c.put("/api/v1/skills/verspec/versions/1",
                        json={"package": _pkg(), "changelog": ""})
            d = (await c.get("/api/v1/skills/verspec")).json()
            assert d["versions"][0]["changelog"] == "第一版（改2）"

            # explicit branch is the only thing that adds a version
            r = await c.post("/api/v1/skills/verspec/versions",
                             json={"package": _pkg(), "changelog": "第二版"})
            assert r.json()["version"] == 2
            d = (await c.get("/api/v1/skills/verspec")).json()
            assert [(v["version"], v["changelog"]) for v in d["versions"]] == \
                [(1, "第一版（改2）"), (2, "第二版")]

            # published versions stay immutable; publishing keeps its own note
            await c.post("/api/v1/skills/verspec/versions/2/publish")
            r = await c.put("/api/v1/skills/verspec/versions/2",
                            json={"package": _pkg(), "changelog": "偷改已发布"})
            assert r.status_code == 409
            d = (await c.get("/api/v1/skills/verspec?version=2")).json()
            assert d["versions"][1]["changelog"] == "第二版"


async def test_model_options_lists_configured_providers_and_parsers(tmp_path, monkeypatch):
    """The editor's model/parser pickers read the server's config; a hardcoded
    frontend list had already drifted from configs/parsers.yaml."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get("/api/v1/skills/model-options")
            assert r.status_code == 200, r.text
            body = r.json()
            names = [p["name"] for p in body["providers"]]
            assert "qwen" in names and len(names) == len(set(names))
            assert sum(1 for p in body["providers"] if p["active"]) == 1
            assert any(p["name"] == "pdfplumber" for p in body["parsers"])
            # parsers ship name/type/description for the editor picker
            assert all(set(p) == {"name", "type", "description"}
                       for p in body["parsers"])
            # never leak key material through this non-admin route
            assert all(set(p) == {"name", "model", "active", "custom", "vision"}
                       for p in body["providers"])
            assert all(p["custom"] is False for p in body["providers"])  # none registered yet


async def test_file_list_filters_narrow_rows_and_total_together(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            from datetime import datetime, timedelta, timezone

            from app.db import session_factory
            from app.models import FileRecord, Transaction
            now = datetime.now(timezone.utc)
            sf = session_factory()
            async with sf() as s:
                for i, (skill, name, status, age) in enumerate([
                        ("inv", "alpha.pdf", "pending_verification", 0),
                        ("inv", "beta.pdf", "passed", 0),
                        ("pii", "alpha-scan.png", "pending_verification", 40)]):
                    s.add(Transaction(id=f"t{i}", tenant_id="default", skill_code=skill,
                                      skill_version=1, status="completed"))
                    s.add(FileRecord(id=f"f{i}", tenant_id="default", transaction_id=f"t{i}",
                                     file_name=name, storage_path=f"/tmp/{name}",
                                     status=status, page_count=1,
                                     created_at=now - timedelta(days=age)))
                await s.commit()

            async def rows(**q):
                r = await c.get("/api/v1/files", params={"page": 1, **q})
                body = r.json()
                assert body["total"] == len(body["data"]), "pager total must match rows"
                return sorted(x["file_name"] for x in body["data"])

            assert await rows() == ["alpha-scan.png", "alpha.pdf", "beta.pdf"]
            assert await rows(q="alpha") == ["alpha-scan.png", "alpha.pdf"]
            assert await rows(q="ALPHA") == ["alpha-scan.png", "alpha.pdf"]  # case-insensitive
            assert await rows(q="pii") == ["alpha-scan.png"]                 # matches skill too
            assert await rows(status="passed") == ["beta.pdf"]
            assert await rows(skill_code="inv") == ["alpha.pdf", "beta.pdf"]
            assert await rows(q="alpha", status="passed") == []
            today = now.date().isoformat()
            assert await rows(date_from=today, date_to=today) == ["alpha.pdf", "beta.pdf"]
            assert (await c.get("/api/v1/files", params={"date_from": "not-a-date"})
                    ).status_code == 400


async def test_delete_archives_skill_and_restore_brings_it_back(tmp_path, monkeypatch):
    """需求7: a deleted skill is an archive entry, not a burial — versions stay
    intact and restore flips the state back to active."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            await c.post("/api/v1/skills", json={"package": _pkg(), "changelog": "v1"})
            await c.post("/api/v1/skills/verspec/versions/1/publish")

            r = await c.delete("/api/v1/skills/verspec")
            assert r.json()["state"] == "deleted"
            # hidden from the default roster…
            assert all(x["skill_code"] != "verspec"
                       for x in (await c.get("/api/v1/skills")).json())
            # …but visible in the archive, with its published version retained
            arch = (await c.get("/api/v1/skills", params={"state": "deleted"})).json()
            assert [x["skill_code"] for x in arch] == ["verspec"]
            assert arch[0]["published_version"] == 1

            r = await c.post("/api/v1/skills/verspec/restore")
            assert r.json() == {"skill_code": "verspec", "state": "active"}
            assert [x["skill_code"] for x in (await c.get("/api/v1/skills")).json()] \
                == ["verspec"]
            # restoring an active skill is a 409, not a silent no-op
            assert (await c.post("/api/v1/skills/verspec/restore")).status_code == 409
            # cross-tenant restore is a 404 (no existence leak)
            assert (await c.post("/api/v1/skills/verspec/restore",
                                 headers={"X-Tenant-Id": "other"})).status_code == 404
            # bogus lifecycle bucket is rejected loudly
            assert (await c.get("/api/v1/skills", params={"state": "nope"})).status_code == 400


async def test_delete_version_only_drafts_and_never_the_last_one(tmp_path, monkeypatch):
    """需求8: 「删除当前版本」 removes exactly one draft; history (published/
    archived) is immutable and a skill must keep at least one version."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            await c.post("/api/v1/skills", json={"package": _pkg(), "changelog": "v1"})
            await c.post("/api/v1/skills/verspec/versions",
                         json={"package": _pkg(), "changelog": "v2"})
            r = await c.delete("/api/v1/skills/verspec/versions/2")
            assert r.status_code == 200
            d = (await c.get("/api/v1/skills/verspec")).json()
            assert [v["version"] for v in d["versions"]] == [1]

            # published versions are history and cannot be deleted
            await c.post("/api/v1/skills/verspec/versions/1/publish")
            r = await c.delete("/api/v1/skills/verspec/versions/1")
            assert r.status_code == 409 and "删除技能" in r.json()["detail"]
            # the last remaining version is protected the same way
            r = await c.delete("/api/v1/skills/verspec/versions/1")
            assert r.status_code == 409
            assert (await c.delete("/api/v1/skills/verspec/versions/99")).status_code == 404


async def test_file_search_treats_like_wildcards_literally(tmp_path, monkeypatch):
    """A filename with `_` must be searched literally, not as a LIKE wildcard."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            from app.db import session_factory
            from app.models import FileRecord, Transaction
            sf = session_factory()
            async with sf() as s:
                s.add(Transaction(id="t0", tenant_id="default", skill_code="inv",
                                  skill_version=1, status="completed"))
                for i, name in enumerate(["SO1_EA_1.pdf", "SO1XEAX1.pdf"]):
                    s.add(FileRecord(id=f"f{i}", tenant_id="default", transaction_id="t0",
                                     file_name=name, storage_path=f"/tmp/{name}",
                                     status="passed", page_count=1))
                await s.commit()
            body = (await c.get("/api/v1/files", params={"q": "SO1_EA"})).json()
            assert [r["file_name"] for r in body["data"]] == ["SO1_EA_1.pdf"]
            assert body["total"] == 1
