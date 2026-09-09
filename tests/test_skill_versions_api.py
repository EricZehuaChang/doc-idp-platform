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


async def test_delete_version_takes_the_selected_one_but_never_a_live_one(tmp_path, monkeypatch):
    """需求8, widened 2026-09-04: 「删除当前版本」 removes the version on screen,
    drafts and archived alike — draft-only left nothing deletable, since every
    archived version in a real console is one some task once ran. Refused only
    while the row is still load-bearing: the published version, the last
    remaining version, and a version an unfinished task pinned."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            await c.post("/api/v1/skills", json={"package": _pkg(), "changelog": "v1"})
            for n in ("v2", "v3"):
                await c.post("/api/v1/skills/verspec/versions",
                             json={"package": _pkg(), "changelog": n})
            r = await c.delete("/api/v1/skills/verspec/versions/2")
            assert r.status_code == 200
            d = (await c.get("/api/v1/skills/verspec")).json()
            assert [v["version"] for v in d["versions"]] == [1, 3]

            # publishing v3 archives v1 — the archived one is exactly what the
            # rail is full of, and it must be removable
            await c.post("/api/v1/skills/verspec/versions/1/publish")
            await c.post("/api/v1/skills/verspec/versions/3/publish")
            d = (await c.get("/api/v1/skills/verspec")).json()
            assert [(v["version"], v["status"]) for v in d["versions"]] == \
                [(1, "archived"), (3, "published")]

            from app.db import session_factory
            from app.models import Transaction
            async with session_factory()() as s:
                # a finished task keeps its results whatever happens to the row
                s.add(Transaction(id="done", tenant_id="default", skill_code="verspec",
                                  skill_version=1, status="completed"))
                # ...but a running one still reloads its package by version
                s.add(Transaction(id="live", tenant_id="default", skill_code="verspec",
                                  skill_version=1, status="processing"))
                await s.commit()
            r = await c.delete("/api/v1/skills/verspec/versions/1")
            assert r.status_code == 409 and "还有 1 个任务在跑" in r.json()["detail"]

            async with session_factory()() as s:
                txn = await s.get(Transaction, "live")
                txn.status = "completed"
                await s.commit()
            r = await c.delete("/api/v1/skills/verspec/versions/1")
            assert r.status_code == 200, r.text

            # the published version and the last remaining one stay put
            r = await c.delete("/api/v1/skills/verspec/versions/3")
            assert r.status_code == 409 and "发布版本" in r.json()["detail"]
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


async def test_skill_state_disable_enable_and_guards(tmp_path, monkeypatch):
    """WP4 (P19-23): PATCH /state flips active<->disabled without touching
    versions; disabled stays on the roster but submission refuses it; deleted
    answers 409; unknown action 422; cross-tenant 404."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.post("/api/v1/skills",
                             json={"package": _pkg(), "changelog": "第一版"})
            assert r.status_code == 201

            r = await c.patch("/api/v1/skills/verspec/state", json={"action": "archive"})
            assert r.status_code == 422

            # disable: the default roster still lists it, greyed out
            r = await c.patch("/api/v1/skills/verspec/state", json={"action": "disable"})
            assert r.status_code == 200 and r.json() == {"skill_code": "verspec",
                                                         "state": "disabled"}
            default = (await c.get("/api/v1/skills")).json()
            assert [s["skill_code"] for s in default] == ["verspec"]
            assert default[0]["state"] == "disabled"
            assert (await c.get("/api/v1/skills", params={"state": "active"})).json() == []
            disabled = (await c.get("/api/v1/skills", params={"state": "disabled"})).json()
            assert [s["skill_code"] for s in disabled] == ["verspec"]

            # submission refuses a disabled skill (process keeps a 404 contract)
            r = await c.post("/api/v1/process",
                             files={"files": ("a.pdf", b"%PDF-1.4 x", "application/pdf")},
                             data={"skill_code": "verspec"})
            assert r.status_code == 404

            # re-enable brings it back to active
            r = await c.patch("/api/v1/skills/verspec/state", json={"action": "enable"})
            assert r.status_code == 200 and r.json()["state"] == "active"

            # once soft-deleted, the state endpoint must not resurrect it
            await c.delete("/api/v1/skills/verspec")
            r = await c.patch("/api/v1/skills/verspec/state", json={"action": "enable"})
            assert r.status_code == 409 and "归档" in r.json()["detail"]
            assert (await c.patch("/api/v1/skills/nope/state",
                                  json={"action": "disable"})).status_code == 404
            r = await c.patch("/api/v1/skills/verspec/state", json={"action": "disable"},
                              headers={"X-Tenant-Id": "other"})
            assert r.status_code == 404


async def test_skill_enable_rechecks_the_plan_seat(tmp_path, monkeypatch):
    """Re-enabling occupies a seat again — the cap is enforced at the edge."""
    from app.billing import engine as billing

    async def _cap_exceeded(s, tenant):
        raise billing.EntitlementExceeded("pro", "skills", 3)

    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            # create and disable run under the real (permissive) cap check
            await c.post("/api/v1/skills",
                         json={"package": _pkg(), "changelog": ""})
            r = await c.patch("/api/v1/skills/verspec/state", json={"action": "disable"})
            assert r.status_code == 200
            # only then arm the cap: re-enabling must hit it
            monkeypatch.setattr(billing, "enforce_skill_cap", _cap_exceeded)
            r = await c.patch("/api/v1/skills/verspec/state", json={"action": "enable"})
            assert r.status_code == 403 and "上限" in r.json()["detail"]
            # the skill stayed disabled — a failed enable flips nothing back
            assert (await c.get("/api/v1/skills")).json()[0]["state"] == "disabled"


async def test_skill_list_projects_description_with_source(tmp_path, monkeypatch):
    """WP5 (P26-29): the roster lists the one-line description taken from the
    highest PUBLISHED version (draft fallback) plus an honest source tag."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.post("/api/v1/skills", json={
                "package": _pkg(description="识别增值税发票的字段与价税合计"),
                "changelog": "第一版"})
            assert r.status_code == 201

            rows = (await c.get("/api/v1/skills")).json()
            assert rows[0]["description"] == "识别增值税发票的字段与价税合计"
            assert rows[0]["description_source"] == "draft_v1"

            await c.post("/api/v1/skills/verspec/versions/1/publish")
            rows = (await c.get("/api/v1/skills")).json()
            assert rows[0]["description_source"] == "published_v1"

            # a newer draft with different wording must NOT override the
            # published description — submitters get v1, the list says so
            r = await c.post("/api/v1/skills/verspec/versions",
                             json={"package": _pkg(description="草稿里的新简介"),
                                   "changelog": "改描述"})
            assert r.status_code == 201
            rows = (await c.get("/api/v1/skills")).json()
            assert rows[0]["description"] == "识别增值税发票的字段与价税合计"
            assert rows[0]["description_source"] == "published_v1"
            assert rows[0]["published_version"] == 1

            # empty description -> nulls, never an empty-string column
            await c.post("/api/v1/skills", json={
                "package": SkillPackage(skill_code="nodesc", name="无简介",
                                        fields=[FieldSpec(name="a")]).model_dump(),
                "changelog": ""})
            rows = (await c.get("/api/v1/skills", params={"state": "active"})).json()
            nd = next(x for x in rows if x["skill_code"] == "nodesc")
            assert nd["description"] is None and nd["description_source"] is None
