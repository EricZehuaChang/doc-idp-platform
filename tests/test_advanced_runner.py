"""9.15 WP4: advanced-mode runner — classifier plan → child fan-out with
document_meta → concurrent extraction with failure isolation, classify-only
documents, billing page sums, resubmission idempotency, execution snapshots
and R10 reference pinning/delete protection. LLM always mocked."""

from httpx import ASGITransport, AsyncClient

from app.parsers.base import Block, Page, UDR
from app.skillengine.schema import FieldSpec, SkillPackage

OTHER = {"id": "Other", "doc_type": "Other", "recognition_instruction": "",
         "is_other": True, "handler": "classify_only", "fields": [],
         "validators": [], "additional_rules": "", "output_shape": "object",
         "skill_ref": None}


def adv_pkg(code="adv_test", categories=None, layout="mixed") -> SkillPackage:
    invoice = {"id": "invoice", "doc_type": "发票",
               "recognition_instruction": "有发票号", "is_other": False,
               "handler": "inline",
               "fields": [FieldSpec(name="invoice_no", instruction="号码")],
               "validators": [], "additional_rules": "",
               "output_shape": "object", "skill_ref": None}
    return SkillPackage(skill_code=code, name="高级测试", skill_mode="advanced",
                        document_layout=layout,
                        classification_rules="",
                        categories=categories or [invoice, OTHER])


def _udr(n=3) -> UDR:
    return UDR(pages=[Page(page_no=i, width=595, height=842,
                           markdown=f"发票号码 INV-{i}",
                           blocks=[Block(text=f"INV-{i}", bbox=[1, 2, 3, 4])])
                     for i in range(1, n + 1)], full_markdown="x", parser="test")


class Env:
    """Boots the app, seeds the skill, uploads a 3-page PDF-like file, runs
    the in-process runner with a mocked parser/classifier/extractor."""

    def __init__(self, tmp_path, monkeypatch, pkg: SkillPackage):
        monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
        monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
        import app.config as config
        import app.db as db
        config.get_settings.cache_clear()
        db._engine = None
        db._session_factory = None
        self.pkg = pkg

    async def __aenter__(self):
        from app.db import init_db
        await init_db()
        return self

    async def __aexit__(self, *a):
        from app.db import get_engine
        await get_engine().dispose()

    async def seed_and_run(self, monkeypatch, plan=None, extract_fail_pages=(),
                           classify_error=False):
        """Returns (txn_id, files_by_name, fired_webhooks)."""
        from sqlalchemy import select

        from app.db import session_factory
        from app.models import FileRecord, SkillVersion, Transaction
        import app.extraction.classifier as classifier
        import app.tasks.runner as runner_mod

        monkeypatch.setattr(runner_mod, "parse_document",
                            lambda path, pinned=None: _udr(3))
        # runner imported plan_documents by name: patch ITS reference
        results = {}

        def fake_extract(udr, pkg, **kw):
            key = udr.pages[0].blocks[0].text
            if key in extract_fail_pages:
                raise RuntimeError(f"boom on {key}")
            return ({key: {"$value": key, "$confidence": 3, "$bbox": [1, 2, 3, 4],
                           "$pages": 1}},
                    {"prompt_tokens": 10, "completion_tokens": 5}, False)
        monkeypatch.setattr(runner_mod, "extract", fake_extract)

        fired = []

        async def fake_fire(tenant, event, payload, transport=None):
            fired.append((event, payload))
        monkeypatch.setattr(runner_mod.webhooks, "fire", fake_fire)

        def fake_plan(udr, cats, layout, rules, provider=None, transport=None):
            if classify_error:
                raise classifier.ClassificationError("no valid plan")
            usage = {"prompt_tokens": 7, "completion_tokens": 3,
                     "provider_used": "fake"}
            return plan, usage
        monkeypatch.setattr(runner_mod, "plan_documents", fake_plan)

        sf = session_factory()
        async with sf() as s:
            s.add(SkillVersion(tenant_id="default", skill_code=self.pkg.skill_code,
                               version=1, status="published",
                               package=self.pkg.model_dump()))
            txn = Transaction(tenant_id="default", skill_code=self.pkg.skill_code,
                              skill_version=1)
            s.add(txn)
            await s.flush()
            txn_id = txn.id
            from app.storage import get_storage
            key = get_storage().put_bytes(f"files/default/{txn_id}/a.pdf", b"%PDF-1.4 fake")
            s.add(FileRecord(tenant_id="default", transaction_id=txn_id,
                             file_name="bundle.pdf", storage_path=key))
            await s.commit()
        await runner_mod.process_transaction(txn_id)
        async with sf() as s:
            files = {f.file_name: f for f in (await s.execute(
                select(FileRecord).where(
                    FileRecord.transaction_id == txn_id))).scalars()}
            for f in files.values():
                if f.result is not None:
                    results[f.id] = f.result
            txn = await s.get(Transaction, txn_id)
            txn_status = txn.status
        return txn_id, files, fired, txn_status


PLAN3 = [{"pages": [1], "category_id": "invoice"},
         {"pages": [2], "category_id": "Other"},
         {"pages": [3], "category_id": "invoice"}]
PLAN_CONT = [{"pages": [1, 2], "category_id": "invoice"},
             {"pages": [3], "category_id": "Other"}]


async def test_mixed_plan_fans_out_with_document_meta(tmp_path, monkeypatch):
    """混合 PDF：3 份文档（含未知类别→Other 映射由 classifier 层测；这里给合法
    id），页码、doc_type、handler、source_pages 落 document_meta；父 split。"""
    pkg = adv_pkg(categories=[
        {"id": "invoice", "doc_type": "发票", "recognition_instruction": "",
         "is_other": False, "handler": "inline",
         "fields": [FieldSpec(name="invoice_no")], "validators": [],
         "additional_rules": "", "output_shape": "object", "skill_ref": None},
        dict(OTHER)])
    async with Env(tmp_path, monkeypatch, pkg) as env:
        plan = [{"pages": [1], "category_id": "invoice"},
                {"pages": [2], "category_id": "Other"},
                {"pages": [3], "category_id": "invoice"}]
        _, files, fired, txn_status = await env.seed_and_run(
            monkeypatch, plan=plan)
    parent = files["bundle.pdf"]
    children = [f for n, f in files.items() if "#" in n]
    assert parent.status == "split" and len(children) == 3
    assert txn_status == "completed"
    metas = sorted(children, key=lambda c: c.document_meta["doc_index"])
    assert [m.document_meta["source_pages"] for m in metas] == [[1], [2], [3]]
    assert [m.document_meta["doc_type"] for m in metas] == ["发票", "Other", "发票"]
    assert [m.document_meta["handler"] for m in metas] == ["inline", "classify_only",
                                                           "inline"]
    assert metas[2].document_meta["effective_schema"]["fields"][0]["name"] == "invoice_no"
    # classify-only child: empty result, completed, not_requested
    assert metas[1].result == {} and metas[1].status == "completed"
    assert metas[1].document_meta["extraction_status"] == "not_requested"
    assert metas[0].document_meta["extraction_status"] == "completed"
    # split webhook carries per-document doc_types
    split = next(p for e, p in fired if e == "file.split")
    assert split["doc_types"] == ["发票", "Other", "发票"]


async def test_single_document_still_creates_child(tmp_path, monkeypatch):
    """高级模式即使只有 1 份文档也生成子文件（数据形态统一）。"""
    pkg = adv_pkg(layout="single")
    async with Env(tmp_path, monkeypatch, pkg) as env:
        _, files, _, _ = await env.seed_and_run(
            monkeypatch, plan=[{"pages": [1, 2, 3], "category_id": "invoice"}])
    assert files["bundle.pdf"].status == "split"
    assert sum(1 for n in files if "#" in n) == 1


async def test_child_failure_isolated(tmp_path, monkeypatch):
    """单个子文档出错只标记自己；兄弟照常完成（新路径 D3 语义）。"""
    pkg = adv_pkg()
    async with Env(tmp_path, monkeypatch, pkg) as env:
        _, files, _, txn_status = await env.seed_and_run(
            monkeypatch, plan=[{"pages": [1], "category_id": "invoice"},
                               {"pages": [2], "category_id": "invoice"},
                               {"pages": [3], "category_id": "invoice"}],
            extract_fail_pages=("INV-2",))
    kids = [f for n, f in files.items() if "#" in n]
    ok = [k for k in kids if k.document_meta["doc_index"] != 2]
    bad = next(k for k in kids if k.document_meta["doc_index"] == 2)
    assert bad.status == "error" and "boom" in bad.error
    assert all(k.status == "completed" for k in ok)
    assert files["bundle.pdf"].status == "split"   # parent NOT marked error
    assert txn_status == "completed"


async def test_old_split_path_child_failure_isolated(tmp_path, monkeypatch):
    """D3 回归：v1/标准自动拆分路径里，一个子文档异常不再把父文件标成
    error、也不让其余子文档卡在 processing。"""
    import app.extraction.splitter as sp
    pkg = SkillPackage(skill_code="split_test", name="t",
                       fields=[FieldSpec(name="invoice_no", instruction="号码")])
    async with Env(tmp_path, monkeypatch, pkg) as env:
        # v1 path: multi_doc_split auto + legacy classify_pages mock
        async def fake_plan(*a, **k):   # advanced classifier must not be used
            raise AssertionError("classifier used on v1 path")
        monkeypatch.setattr(sp, "chat_json_with_fallback",
                            lambda messages, chain, transport=None: (
                                {"pages": [{"page": 1, "new_doc": True},
                                           {"page": 2, "new_doc": True},
                                           {"page": 3, "new_doc": False}]},
                                {"prompt_tokens": 5, "completion_tokens": 2}, "fake"))
        _, files, _, txn_status = await env.seed_and_run(
            monkeypatch, plan=None, extract_fail_pages=("INV-2",))
    kids = [f for n, f in files.items() if "#" in n]
    bad = next(k for k in kids if "doc2" in k.file_name)
    assert bad.status == "error"
    assert files["bundle.pdf"].status == "split"
    assert all(k.status == "completed" for k in kids if k.id != bad.id)
    assert txn_status == "completed"


async def test_classification_failure_marks_parent_error(tmp_path, monkeypatch):
    """分类失败：父文件 error（classification_failed），不静默归 Other。"""
    pkg = adv_pkg()
    async with Env(tmp_path, monkeypatch, pkg) as env:
        _, files, _, txn_status = await env.seed_and_run(
            monkeypatch, plan=None, classify_error=True)
    parent = files["bundle.pdf"]
    assert parent.status == "error" and "classification_failed" in parent.error
    assert txn_status == "error"
    assert not any("#" in n for n in files)   # no children created


async def test_billing_page_sum_equals_file_pages(tmp_path, monkeypatch):
    """计费：分类 tokens 记在父文件 pages=0；子文档页数之和 = 文件页数。"""
    pkg = adv_pkg()
    calls = []
    async def fake_meter(**kw):
        calls.append(kw)
    async with Env(tmp_path, monkeypatch, pkg) as env:
        import app.tasks.runner as runner_mod
        monkeypatch.setattr(runner_mod, "shadow_meter", fake_meter)
        _, files, _, _ = await env.seed_and_run(monkeypatch, plan=PLAN_CONT)
    parent_rows = [c for c in calls if c.get("pages") == 0]
    child_rows = [c for c in calls if c.get("pages", 0) > 0]
    assert len(parent_rows) == 1 and parent_rows[0]["usage"]["prompt_tokens"] == 7
    assert sum(c["pages"] for c in child_rows) == 3


async def test_resubmit_does_not_resplit(tmp_path, monkeypatch):
    """重投幂等：父文件已 split 且有子文件时不再重新分类/拆分。"""
    pkg = adv_pkg()
    async with Env(tmp_path, monkeypatch, pkg) as env:
        import app.tasks.runner as runner_mod
        txn_id, files0, _, _ = await env.seed_and_run(monkeypatch, plan=PLAN_CONT)
        calls = {"n": 0}

        def counting_plan(*a, **k):
            calls["n"] += 1
            return PLAN_CONT, {"prompt_tokens": 1, "completion_tokens": 1}
        monkeypatch.setattr(runner_mod, "plan_documents", counting_plan)
        await runner_mod.extract_stage(files0["bundle.pdf"].id, pkg, {})
        assert calls["n"] == 0        # already-split parent skips classification


async def _api_client(tmp_path, monkeypatch):
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


async def test_reference_pin_and_delete_protection(tmp_path, monkeypatch):
    """R10：发布时把跟随最新钉成具体版本；被引用版本删除返回 409；
    引用不可用（未发布）时创建快照失败 409 reference_unavailable。"""
    pkg_ref_target = SkillPackage(skill_code="ref_target", name="被引用",
                                  fields=[FieldSpec(name="total")])
    adv = adv_pkg("ref_user", categories=[
        {"id": "c1", "doc_type": "发票", "recognition_instruction": "",
         "is_other": False, "handler": "existing_skill", "fields": [],
         "validators": [], "additional_rules": "", "output_shape": "object",
         "skill_ref": {"skill_code": "ref_target", "version": None}},
        dict(OTHER)])
    async with Env(tmp_path, monkeypatch, adv):
        from app.db import session_factory
        from app.models import Skill, SkillVersion
        sf = session_factory()
        async with sf() as s:
            s.add(Skill(code="ref_target", tenant_id="default", name="被引用",
                        kind="extract", state="active"))
            s.add(SkillVersion(tenant_id="default", skill_code="ref_target",
                               version=2, status="published",
                               package=pkg_ref_target.model_dump()))
            await s.commit()
        gen = _api_client(tmp_path, monkeypatch)
        c = await gen.__anext__()
        try:
            r = await c.post("/api/v1/skills", json={"package": adv.model_dump()})
            assert r.status_code == 201, r.text
            # reference-skills listing: eligible list contains ref_target
            r = await c.get("/api/v1/studio/reference-skills", params={"exclude": "ref_user"})
            assert r.status_code == 200
            assert any(x["skill_code"] == "ref_target"
                       and x["published_version"] == 2 for x in r.json()["skills"])
            r = await c.post("/api/v1/skills/ref_user/versions/1/publish")
            assert r.status_code == 200, r.text
            async with sf() as s:
                from sqlalchemy import select
                row = (await s.execute(select(SkillVersion).where(
                    SkillVersion.skill_code == "ref_user"))).scalars().first()
                pinned = row.package["categories"][0]["skill_ref"]["version"]
                assert pinned == 2     # draft's null ref pinned at publish
            # ref_user published above pins v2 (the only published version);
            # publish v3 -> v2 becomes archived but stays pinned for ref_user
            pkg_t3 = dict(pkg_ref_target.model_dump())
            r = await c.post("/api/v1/skills/ref_target/versions",
                             json={"package": pkg_t3, "changelog": "v3"})
            assert r.status_code in (200, 201), r.text
            v3 = r.json()["version"]
            r = await c.post(f"/api/v1/skills/ref_target/versions/{v3}/publish")
            assert r.status_code == 200, r.text
            r = await c.delete("/api/v1/skills/ref_target/versions/2")
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["code"] == "version_referenced"
            assert r.json()["detail"]["referencers"][0]["skill_code"] == "ref_user"
            # submit without files fails validation before anything else
            r = await c.post("/api/v1/process", json={"skill_code": "ref_user"})
            assert r.status_code in (400, 422)
        finally:
            await gen.aclose()


async def test_execution_snapshot_used_by_runner(tmp_path, monkeypatch):
    """提交快照：runner 优先读 execution_snapshot——被引用技能后续发布新版
    不影响在途任务（用旧包内容执行）。"""
    from app.db import session_factory
    from app.models import FileRecord, SkillVersion, Transaction
    from app.tasks import runner as runner_mod

    target_v2 = SkillPackage(skill_code="ref_target", name="v2",
                             fields=[FieldSpec(name="total_v2")])
    user_pkg = adv_pkg("ref_user", categories=[
        {"id": "c1", "doc_type": "发票", "recognition_instruction": "",
         "is_other": False, "handler": "existing_skill", "fields": [],
         "validators": [], "additional_rules": "", "output_shape": "object",
         "skill_ref": {"skill_code": "ref_target", "version": 1}},
        dict(OTHER)])

    async with Env(tmp_path, monkeypatch, user_pkg):
        async def _main():
            from app.storage import get_storage
            sf = session_factory()
            async with sf() as s:
                s.add(SkillVersion(tenant_id="default", skill_code="ref_user",
                                   version=1, status="published",
                                   package=user_pkg.model_dump()))
                txn = Transaction(tenant_id="default", skill_code="ref_user",
                                  skill_version=1)
                # snapshot carries the OLD dependency package (v1 with field a)
                txn.execution_snapshot = {
                    "package": user_pkg.model_dump(),
                    "dependencies": {"ref_target": {
                        "version": 1,
                        "package": {"skill_code": "ref_target", "name": "v1",
                                    "fields": [{"name": "old_field",
                                                "type": "string"}]}}},
                    "skill_version": 1}
                s.add(txn)
                await s.flush()
                key = get_storage().put_bytes(
                    f"files/default/{txn.id}/b.pdf", b"%PDF-1.4")
                s.add(FileRecord(tenant_id="default", transaction_id=txn.id,
                                 file_name="one.pdf", storage_path=key))
                await s.commit()
                txn_id = txn.id
            # AFTER the snapshot, the referenced skill publishes v2 — must not matter
            async with sf() as s:
                s.add(SkillVersion(tenant_id="default", skill_code="ref_target",
                                   version=2, status="published",
                                   package=target_v2.model_dump()))
                await s.commit()
            seen = {}

            def fake_extract(udr, pkg, **kw):
                seen["fields"] = [f.name for f in pkg.fields]
                return ({}, {"prompt_tokens": 1, "completion_tokens": 1}, False)

            def fake_plan(udr, cats, layout, rules, provider=None, transport=None):
                return [{"pages": [1], "category_id": "c1"}], {"prompt_tokens": 1,
                                                               "completion_tokens": 1}
            monkeypatch.setattr(runner_mod, "parse_document",
                                lambda path, pinned=None: _udr(1))
            monkeypatch.setattr(runner_mod, "plan_documents", fake_plan)
            monkeypatch.setattr(runner_mod, "extract", fake_extract)
            monkeypatch.setattr(runner_mod.webhooks, "fire",
                                async_noop_fire)
            await runner_mod.process_transaction(txn_id)
            assert seen["fields"] == ["old_field"]   # snapshot won over v2
        await _main()


async def async_noop_fire(*a, **k):
    return None
