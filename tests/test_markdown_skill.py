"""A published PDF-to-Markdown skill exposes its saved OCR result only to its caller."""
import asyncio
from io import BytesIO


from app.auth import security
from app.models import ApiKey, FileRecord, SkillVersion, Transaction
from app.parsers.base import Block, Page, UDR
from app.skillengine import catalog
from app.skillengine.schema import SkillPackage
from app.skillengine.validation import validate_package
from app.storage import get_storage
from tests.test_delete_task import _seed_user
from tests.test_wp5_fast_and_playground import _boot

FULL = "# 第 1 页  \n\n- A & B\n\n<table><tr><td>第 2 页</td></tr></table>\n"
PACKAGE = {"kind": "layout", "include_markdown": True, "fields": []}


def test_template_and_opt_in_constraints():
    pkg = catalog.get_template("pdf_to_markdown")
    assert pkg and pkg.include_markdown and pkg.kind == "layout" and pkg.fields == []
    assert pkg.parser == "glm-ocr-cloud" and validate_package(pkg, stage="publish")[0] == []
    assert not SkillPackage(skill_code="legacy").include_markdown
    for kind, fields in (("extract", []), ("layout", [{"name": "total"}])):
        invalid = SkillPackage(skill_code="invalid", kind=kind, fields=fields, include_markdown=True)
        for stage in ("save", "publish"):
            errors, _ = validate_package(invalid, stage=stage)
            assert any(e["path"] == "include_markdown" for e in errors)


async def views(client, txn_id, headers=None):
    status = await client.get(f"/api/v1/status/{txn_id}", headers=headers)
    docs = await client.get(f"/api/v1/transactions/{txn_id}/documents", headers=headers)
    assert (status.status_code, docs.status_code) == (200, 200), (status.text, docs.text)
    root = docs.json()["files"][0]
    assert "markdown" not in root  # Each document owns its own OCR output.
    return status.json()["files"][0], root["documents"][0]


async def seed(db, txn_id, snapshot, udr_path, *, status="completed", key=None):
    async with db.session_factory()() as s:
        txn = Transaction(id=txn_id, skill_code="markdown-fixture", skill_version=1, status=status,
                          purpose="production", tenant_id="default", api_key_id=key, execution_snapshot=snapshot)
        s.add(txn)
        await s.flush()
        s.add(FileRecord(transaction_id=txn_id, tenant_id="default", storage_path="fixture.pdf",
                         file_name="fixture.pdf", udr_path=udr_path, status=status, page_count=2, result={}))
        await s.commit()


async def test_published_template_processes_a_two_page_scan_once(tmp_path, monkeypatch):
    from PIL import Image
    from app.extraction import splitter
    from app.tasks import runner

    pinned = []
    source = UDR(parser="glm-ocr-cloud", full_markdown=FULL, pages=[
        Page(page_no=1, markdown="page fallback one"), Page(page_no=2, markdown="page fallback two")])

    def ocr(path, pinned_parser):
        pinned.append(pinned_parser)
        return source

    def unexpected(*args, **kwargs):
        raise AssertionError("Pure Markdown conversion reached a classifier or field extractor")

    monkeypatch.setattr(runner, "parse_document", ocr)
    monkeypatch.setattr(splitter, "classify_pages", unexpected)
    monkeypatch.setattr(runner, "extract", unexpected)
    image = Image.new("RGB", (16, 16), "white")
    pdf = BytesIO()
    image.save(pdf, format="PDF", save_all=True, append_images=[image])
    async for client, _ in _boot(tmp_path, monkeypatch):
        draft = await client.post("/api/v1/skills/templates/pdf_to_markdown/import")
        assert draft.status_code == 201, draft.text
        code = draft.json()["skill_code"]
        assert (await client.post(f"/api/v1/skills/{code}/versions/1/publish")).status_code == 200
        uploaded = await client.post("/api/v1/process", files={"files": ("scan.pdf", pdf.getvalue(), "application/pdf")},
                                     data={"skill_code": code})
        assert uploaded.status_code == 202, uploaded.text
        txn = uploaded.json()["transaction_id"]
        for _ in range(100):
            current = (await client.get(f"/api/v1/status/{txn}")).json()
            if current["status"] in {"completed", "error"}:
                break
            await asyncio.sleep(0.05)
        assert current["status"] == "completed", current
        file, doc = await views(client, txn)
        assert file["markdown"] == doc["markdown"] == FULL
        assert file["page_count"] == 2 and file["result"] == doc["data"] == {}
        assert file["input_tokens"] == file["output_tokens"] == 0
        assert file["layout"]["parser"] == "glm-ocr-cloud" and pinned == ["glm-ocr-cloud"]


async def test_snapshot_controls_exposure_and_readiness_controls_null(tmp_path, monkeypatch):
    async for client, db in _boot(tmp_path, monkeypatch):
        storage = get_storage()
        key = storage.put_bytes("udr/read.json", UDR(full_markdown=FULL).model_dump_json().encode())
        invalid = storage.put_bytes("udr/bad.json", b"incomplete json")
        # Old snapshots must retain their response shape even after a version enables the new output.
        async with db.session_factory()() as session:
            session.add(SkillVersion(skill_code="markdown-fixture", version=1, tenant_id="default",
                                     status="published", package=PACKAGE))
            await session.commit()
        for name, snapshot, layout in [
            ("no-snapshot", None, False),
            ("original", {"package": {"kind": "extract"}}, False),
            ("layout", {"package": {"kind": "layout"}}, True),
            ("disabled", {"package": {**PACKAGE, "include_markdown": False}}, True),
            ("bad-kind", {"package": {**PACKAGE, "kind": "extract"}}, False),
            ("has-fields", {"package": {**PACKAGE, "fields": [{"name": "x"}]}}, True),
        ]:
            await seed(db, name, snapshot, key)
            for view in await views(client, name):
                assert "markdown" not in view and ("layout" in view) == layout
        for name, state, path in [
            ("queued", "queued", None), ("pending-udr", "processing", key), ("failed", "error", key),
            ("missing", "completed", "udr/absent.json"), ("invalid", "passed", invalid),
        ]:
            await seed(db, name, {"package": PACKAGE}, path, status=state)
            for view in await views(client, name):
                assert view["markdown"] is None


async def test_fallback_preserves_page_order_and_distinguishes_empty_output(tmp_path, monkeypatch):
    async for client, db in _boot(tmp_path, monkeypatch):
        for name, pages, expected in [
            ("fallback", [Page(page_no=1, markdown="## 标题", blocks=[Block(text="duplicate")]),
                          Page(page_no=2, blocks=[Block(text="段落一"), Block(text="段落二")])],
             "## 标题\n\n段落一\n段落二"),
            ("blank", [Page(page_no=1), Page(page_no=2)], ""),
        ]:
            body = UDR(pages=pages).model_dump_json().encode()
            path = get_storage().put_bytes(f"udr/{name}.json", body)
            await seed(db, name, {"package": PACKAGE}, path, status="passed")
            for view in await views(client, name):
                assert view["markdown"] == expected


async def test_api_keys_can_read_only_their_own_markdown(tmp_path, monkeypatch):
    async for client, db in _boot(tmp_path, monkeypatch, auth_on=True):
        headers = {}
        for name, tenant in [("owner", "default"), ("other-owner", "default"), ("foreign", "another")]:
            user = await _seed_user(f"{name}@example.com", "operator", tenant)
            token, prefix, hashed = security.generate_api_key()
            async with db.session_factory()() as session:
                session.add(ApiKey(id=name, tenant_id=tenant, name="same label", owner_user_id=user["id"],
                                   key_type="agent", prefix=prefix, key_hash=hashed))
                await session.commit()
            headers[name] = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"}
        key = get_storage().put_bytes("udr/protected.json", UDR(full_markdown=FULL).model_dump_json().encode())
        await seed(db, "protected", {"package": PACKAGE}, key, key="owner")
        for view in await views(client, "protected", headers["owner"]):
            assert view["markdown"] == FULL
        for outsider in ("other-owner", "foreign"):
            for route in ("/api/v1/status/protected", "/api/v1/transactions/protected/documents"):
                response = await client.get(route, headers=headers[outsider])
                assert response.status_code == 404 and FULL not in response.text
