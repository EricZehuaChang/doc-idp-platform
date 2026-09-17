"""FX1 (走查 P0 修复) 回归测试：#1 自动生成字段、#2 中文名下载、#3 极速视觉分支、
#4 拆分产出、#8 高级重命名。

约定（修复要求 §0.4）：只替换最外层 I/O —— 对话调用替换
`chat_json_with_fallback`（pipeline / classifier 各自导入的那一份），解析、分类
编排、抽取、产出全部执行真实函数体。文件名夹具含中文、空格与 `#`。
"""
from __future__ import annotations

import asyncio
import io
import re

import pytest
from httpx import ASGITransport, AsyncClient



# ---------------------------------------------------------------- #1 生成字段


def test_generate_fields_real_shape(monkeypatch):
    """真实 studio.generate_fields + 真实 draft_to_fields，只替换对话调用。"""
    from app.skillengine import studio

    real_shape = {"fields": [
        {"name": "invoice_no", "type": "string", "instruction": "发票号码",
         "mode": "verbatim", "required": True, "example_value": "6600749715"},
        {"name": "invoice_date", "type": "date", "instruction": "开票日期",
         "mode": "verbatim", "example_value": "2026-09-17"},
        {"name": "items", "type": "table", "instruction": "明细行",
         "columns": [{"name": "description", "instruction": "品名"},
                     {"name": "amount", "instruction": "金额"}]},
    ]}
    monkeypatch.setattr(studio, "chat_json_with_fallback",
                        lambda messages, chain, transport=None:
                        (real_shape, {"total_tokens": 42}, "qwen"))
    out = studio.generate_fields("发票号码 6600749715", "抽发票号、日期和明细")
    names = [f.name for f in out["fields"]]
    assert names == ["invoice_no", "invoice_date", "items"]
    assert out["fields"][0].instruction == "发票号码"
    assert out["fields"][0].required is True
    assert out["fields"][1].type == "date"
    assert [c.name for c in out["fields"][2].columns] == ["description", "amount"]
    assert out["examples"] == {"invoice_no": "6600749715",
                               "invoice_date": "2026-09-17"}
    assert out["provider_used"] == "qwen"


@pytest.mark.parametrize("bad", [
    {"fields": []},                     # 模型没给字段
    ["not", "a", "dict"],               # 非字典
    {"fields": {"name": "x"}},          # fields 不是列表
    {"fields": [{"type": "string"}]},   # 字段缺 name
    {"fields": "oops"},
    None,
])
def test_generate_fields_malformed_output_never_raises(monkeypatch, bad):
    from app.skillengine import studio

    monkeypatch.setattr(studio, "chat_json_with_fallback",
                        lambda messages, chain, transport=None:
                        (bad, {}, "qwen"))
    out = studio.generate_fields("样本", "描述")
    assert out["fields"] == [] and out["examples"] == {}


async def test_generate_fields_route_uses_real_function(tmp_path, monkeypatch):
    """路由用例走真实 generate_fields（此前测试把整个函数替换成了假实现）。"""
    from app.db import init_db, session_factory
    from app.models import Skill, StudioSample
    from app.skillengine import studio
    from app.storage import get_storage

    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    await init_db()
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        st = get_storage()
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(595, 842))
        c.setFont("Helvetica", 12)
        c.drawString(72, 800, "Invoice total 100.00 EUR")
        c.showPage()
        c.save()
        key = st.put_bytes("studio/default/s1/orig.pdf", buf.getvalue())
        async with session_factory()() as s:
            s.add(Skill(code="fx1", tenant_id="default", name="fx1", kind="extract"))
            s.add(StudioSample(id="s1", tenant_id="default", skill_code="fx1",
                               file_name="发票 #1 测试.pdf", storage_key=key,
                               uploader_id="tester"))
            await s.commit()
        monkeypatch.setattr(studio, "chat_json_with_fallback",
                            lambda messages, chain, transport=None:
                            ({"fields": [{"name": "total", "type": "number",
                                          "instruction": "合计金额",
                                          "example_value": "100.00"}]},
                             {"total_tokens": 5}, "qwen"))
        # only the chat call is replaced — the sample goes through the REAL
        # parser (a genuine PDF with a text layer)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.post("/api/v1/studio/generate-fields",
                             json={"sample_id": "s1", "description": "抽合计金额"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert [f["name"] for f in body["fields"]] == ["total"]
            assert body["examples"] == {"total": "100.00"}


# ------------------------------------------------------- #2 中文文件名下载


def test_content_disposition_encodes_non_ascii():
    from app.api.http_headers import content_disposition
    from urllib.parse import unquote
    name = "发票 #1-测试 2026.pdf"
    header = content_disposition(name)
    assert header.isascii(), "响应头必须是纯 ASCII（Starlette 用 latin-1 编码）"
    assert "filename*=UTF-8''" in header
    assert unquote(header.split("UTF-8''", 1)[1]) == name
    assert "\r" not in header and "\n" not in header
    injected = content_disposition("a\r\nX-Evil: 1.pdf")
    assert "\r" not in injected and "\n" not in injected


async def test_artifact_download_chinese_name(tmp_path, monkeypatch):
    """真实下载请求：产出名含中文、空格和 #，必须 200 且头为 ASCII。"""

    from app.db import init_db, session_factory
    from app.models import FileArtifact, FileRecord, Transaction
    from app.storage import get_storage
    from urllib.parse import unquote

    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    await init_db()
    st = get_storage()
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    c.drawString(72, 800, "INV-A")
    c.showPage()
    c.save()
    name = "发票 #1-测试.pdf"
    async with session_factory()() as s:
        txn = Transaction(tenant_id="default", skill_code="fx1", skill_version=1)
        s.add(txn)
        await s.flush()
        key = st.put_bytes(f"files/default/{txn.id}/a.pdf", buf.getvalue())
        f = FileRecord(tenant_id="default", transaction_id=txn.id,
                       file_name="a.pdf", storage_path=key, status="completed")
        s.add(f)
        await s.flush()
        akey = st.put_bytes(f"artifacts/default/{f.id}/x.pdf", buf.getvalue())
        s.add(FileArtifact(id="art1", tenant_id="default", file_id=f.id,
                           action="rename", display_name=name, storage_key=akey,
                           status="ready", size=len(buf.getvalue())))
        await s.commit()
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get("/api/v1/artifacts/art1/download")
            assert r.status_code == 200, r.text
            assert r.content.startswith(b"%PDF")
            header = r.headers["content-disposition"]
            assert header.isascii()
            assert unquote(header.split("UTF-8''", 1)[1]) == name


# --------------------------------------------------- #3 极速模式视觉分支


def _png(path, w=600, h=400):
    from PIL import Image
    Image.new("RGB", (w, h), "white").save(path)
    return path


def test_fast_parse_png_uses_vision_channel(tmp_path, monkeypatch):
    """真实 _fast_parse：只替换视觉通道名（配置）。"""
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    config.get_settings.cache_clear()
    from app.tasks import runner

    monkeypatch.setattr(runner, "_fast_vision_provider", lambda tenant: "qwen-vl")
    png = _png(tmp_path / "出租车票 截图.png", 600, 400)
    udr, images, route = asyncio.run(runner._fast_parse(str(png), "default"))
    assert route == "vision"
    assert list(images) == [1]
    assert images[1].startswith("data:image/")
    assert len(udr.pages) == 1
    assert (udr.pages[0].width, udr.pages[0].height) == (600.0, 400.0)
    assert udr.lang == [] and udr.parser == "vision_fast"


def test_fast_parse_scanned_pdf_uses_vision_channel(tmp_path, monkeypatch):
    """无文本层的 PDF：页图逐页进视觉通道，页尺寸取自原件。"""
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    config.get_settings.cache_clear()
    from app.tasks import runner

    monkeypatch.setattr(runner, "_fast_vision_provider", lambda tenant: "qwen-vl")
    # image-only PDF (no text layer) built from a raster page
    from PIL import Image
    from reportlab.pdfgen import canvas
    img = tmp_path / "page.png"
    Image.new("RGB", (595, 842), "white").save(img)
    pdf = tmp_path / "扫描件 测试.pdf"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    c.drawImage(str(img), 0, 0, width=595, height=842)
    c.showPage()
    c.drawImage(str(img), 0, 0, width=595, height=842)
    c.showPage()
    c.save()
    pdf.write_bytes(buf.getvalue())

    udr, images, route = asyncio.run(runner._fast_parse(str(pdf), "default"))
    assert route == "vision"
    assert sorted(images) == [1, 2]
    assert len(udr.pages) == 2
    assert udr.pages[0].width == pytest.approx(595, abs=1)
    assert udr.pages[0].height == pytest.approx(842, abs=1)


# ------------------------------------------------- #4 / #8 端到端产出


def _multi_doc_pdf(path, plan) -> str:
    """plan: [(page_text, pages)] -> 真实多页 PDF。页文本里带着被抽取的值，
    置信度由真实的 score_field 定位得到（不是假造的分数）。"""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    for label, n in plan:
        for i in range(n):
            c.setFont("Helvetica", 12)
            c.drawString(72, 800, f"{label} 第{i + 1}页")
            c.showPage()
    c.save()
    path.write_bytes(buf.getvalue())
    return str(path)


def _pdf_pages(blob: bytes) -> int:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        return len(pdf.pages)


class AdvEnv:
    """真实 runner + 真实 output_stage；只替换两处对话调用（分类、抽取）。"""

    def __init__(self, tmp_path, monkeypatch, *, review_mode="never",
                 action="split", naming_rule="", searchable=False):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.review_mode = review_mode
        self.action = action
        self.naming_rule = naming_rule
        self.searchable = searchable
        self.extract_calls: list[str] = []

    async def __aenter__(self):
        monkeypatch = self.monkeypatch
        monkeypatch.setenv("IDP_DATABASE_URL",
                           f"sqlite+aiosqlite:///{self.tmp_path}/t.db")
        monkeypatch.setenv("IDP_DATA_DIR", str(self.tmp_path))
        import app.config as config
        import app.db as db
        config.get_settings.cache_clear()
        db._engine = None
        db._session_factory = None
        from app.db import init_db
        await init_db()
        return self

    async def __aexit__(self, *a):
        from app.db import get_engine
        await get_engine().dispose()

    def _patch_chat(self, missing_invoice=False):
        """分类与抽取共用同一个最外层 I/O 替换点（各自模块导入的那份）。"""
        import app.extraction.classifier as classifier
        import app.extraction.pipeline as pipeline

        def fake_classify(messages, chain, transport=None):
            prompt = "\n".join(m.get("content", "") for m in messages)
            pages = [int(m) for m in re.findall(r"第(\d+)页开头", prompt)]
            cat_of = {1: "c_inv", 2: "c_inv", 3: "c_pack", 4: "c_pack",
                      5: "c_pack", 6: "Other", 7: "Other"}
            starts = {1, 3, 6}
            out = {"pages": [{"page": p, "category_id": cat_of.get(p, "Other"),
                              "new_doc": p in starts} for p in pages]}
            return out, {"prompt_tokens": 11, "completion_tokens": 5}, "fake-vlm"

        def fake_extract_chat(messages, chain, transport=None):
            prompt = "\n".join(m.get("content", "") for m in messages)
            self.extract_calls.append(prompt)
            return ({"invoice_no": "NOT-IN-TEXT" if missing_invoice else "INV-1001",
                     "case_no": "CASE-1"},
                    {"prompt_tokens": 20, "completion_tokens": 8}, "fake-llm")

        self.monkeypatch.setattr(classifier, "chat_json_with_fallback",
                                 fake_classify)
        self.monkeypatch.setattr(pipeline, "chat_json_with_fallback",
                                 fake_extract_chat)

    async def seed(self, plan=(("商业发票 发票号码 INV-1001", 2),
                              ("装箱单 箱号 CASE-1", 3),
                              ("其他资料 OTHER", 2))):
        """发布一个高级技能（拆分或重命名产出）并把多文档 PDF 入队。"""

        from app.db import session_factory
        from app.models import FileRecord, Skill, SkillVersion, Transaction
        from app.skillengine.schema import FieldSpec, SkillPackage
        from app.storage import get_storage

        pkg = SkillPackage(
            skill_code="fx_adv", name="FX 高级", skill_mode="advanced",
            document_layout="mixed",
            review_policy={"mode": self.review_mode,
                           "confidence_threshold": 2},
            output={"enabled": True, "action": self.action,
                    "naming_rule": self.naming_rule,
                    "searchable_pdf": self.searchable},
            categories=[
                {"id": "c_inv", "doc_type": "商业发票",
                 "recognition_instruction": "有发票号", "is_other": False,
                 "handler": "inline",
                 "fields": [FieldSpec(name="invoice_no", instruction="发票号码")],
                 "validators": [], "additional_rules": "",
                 "output_shape": "object", "skill_ref": None},
                {"id": "c_pack", "doc_type": "装箱单",
                 "recognition_instruction": "有箱号", "is_other": False,
                 "handler": "inline",
                 "fields": [FieldSpec(name="case_no", instruction="箱号")],
                 "validators": [], "additional_rules": "",
                 "output_shape": "object", "skill_ref": None},
                {"id": "Other", "doc_type": "Other",
                 "recognition_instruction": "", "is_other": True,
                 "handler": "classify_only", "fields": [], "validators": [],
                 "additional_rules": "", "output_shape": "object",
                 "skill_ref": None}])
        pdf = _multi_doc_pdf(self.tmp_path / "25041081 CI PL 测试#1.pdf", list(plan))
        st = get_storage()
        async with session_factory()() as s:
            s.add(Skill(code="fx_adv", tenant_id="default", name="FX 高级",
                        kind="extract"))
            s.add(SkillVersion(tenant_id="default", skill_code="fx_adv",
                               version=1, status="published",
                               package=pkg.model_dump()))
            txn = Transaction(tenant_id="default", skill_code="fx_adv",
                              skill_version=1, purpose="test")
            s.add(txn)
            await s.flush()
            with open(pdf, "rb") as fh:
                key = st.put_bytes(f"files/default/{txn.id}/src.pdf", fh.read())
            s.add(FileRecord(tenant_id="default", transaction_id=txn.id,
                             file_name="25041081 CI PL 测试#1.pdf",
                             storage_path=key, page_count=sum(n for _, n in plan),
                             status="queued"))
            await s.commit()
            self.txn_id = txn.id
        return self.txn_id

    async def run(self):
        import app.tasks.runner as runner_mod
        self._patch_chat(missing_invoice=getattr(self, "missing_invoice", False))
        await runner_mod.process_transaction(self.txn_id)


async def _txn_rows(txn_id):
    from sqlalchemy import select

    from app.db import session_factory
    from app.models import FileArtifact, FileRecord
    from app.storage import get_storage
    async with session_factory()() as s:
        files = (await s.execute(
            select(FileRecord).where(FileRecord.transaction_id == txn_id)
            .order_by(FileRecord.created_at, FileRecord.id))).scalars().all()
        arts = (await s.execute(
            select(FileArtifact).order_by(FileArtifact.created_at))).scalars().all()
    blobs = {a.id: get_storage().read_bytes(a.storage_key)
             for a in arts if a.status == "ready" and a.storage_key}
    return files, arts, blobs


async def test_split_produces_one_artifact_per_document(tmp_path, monkeypatch):
    """#4：三份文档（含一份仅分类）→ 3 个产出，页数等于各自 source_pages。"""
    async with AdvEnv(tmp_path, monkeypatch, action="split",
                      review_mode="never") as env:
        txn_id = await env.seed()
        await env.run()
        files, arts, blobs = await _txn_rows(txn_id)
        children = [f for f in files if f.parent_file_id]
        assert [f.status for f in children] == ["completed"] * 3
        assert [(f.document_meta or {}).get("doc_index") for f in children] == [1, 2, 3]
        assert len(arts) == 3, [(a.display_name, a.status, a.error) for a in arts]
        assert all(a.status == "ready" for a in arts), \
            [(a.display_name, a.error) for a in arts]
        # 每个产出挂在对应的子文档上，页数 = 该文档的 source_pages
        by_file = {a.file_id: a for a in arts}
        for child in children:
            meta = child.document_meta or {}
            assert child.id in by_file, child.file_name
            assert _pdf_pages(blobs[by_file[child.id].id]) == len(meta["source_pages"])
        # 命名使用该文档自己的 doc_index/doc_type
        names = sorted(a.display_name for a in arts)
        assert names[0].startswith("01_商业发票_")
        assert names[1].startswith("02_装箱单_")
        assert names[2].startswith("03_Other_")
        assert all(a.doc_index in (1, 2, 3) for a in arts)


async def test_split_single_document_file_produces_one(tmp_path, monkeypatch):
    """T35：只有一份文档的高级文件也要产出 1 个。"""
    async with AdvEnv(tmp_path, monkeypatch, action="split",
                      review_mode="never") as env:
        txn_id = await env.seed(plan=(("商业发票", 2),))
        await env.run()
        files, arts, blobs = await _txn_rows(txn_id)
        assert len(arts) == 1, [(a.display_name, a.status, a.error) for a in arts]
        assert arts[0].status == "ready" and arts[0].doc_index == 1
        assert _pdf_pages(blobs[arts[0].id]) == 2


async def test_split_waits_for_review_then_generates(tmp_path, monkeypatch):
    """#4 之二：待复核的文档暂不产出，审单通过后其产出出现。"""
    async with AdvEnv(tmp_path, monkeypatch, action="split",
                      review_mode="auto") as env:
        env.missing_invoice = True       # 第 1 份文档的字段在原文里找不到 -> 待复核
        txn_id = await env.seed()
        await env.run()
        files, arts, blobs = await _txn_rows(txn_id)
        children = {f.id: f for f in files if f.parent_file_id}
        pending = [f for f in children.values()
                   if f.status == "pending_verification"]
        assert len(pending) == 1, [(f.file_name, f.status) for f in children.values()]
        # 只有已完成的文档有产出（其余两份）
        assert len(arts) == 2, [(a.display_name, a.status) for a in arts]
        assert pending[0].id not in {a.file_id for a in arts}

        # —— 审单通过：产出出现 ——
        from app.main import create_app
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://test") as c:
                r = await c.post(f"/api/v1/review/{pending[0].id}/confirm",
                                 headers={"X-User": "tester"})
                assert r.status_code == 200, r.text
                await asyncio.sleep(0)
        files2, arts2, blobs2 = await _txn_rows(txn_id)
        assert len(arts2) == 3, [(a.display_name, a.status, a.error) for a in arts2]
        new = [a for a in arts2 if a.file_id == pending[0].id]
        assert len(new) == 1 and new[0].status == "ready"
        assert _pdf_pages(blobs2[new[0].id]) == len(
            (children[pending[0].id].document_meta or {})["source_pages"])


async def test_advanced_rename_is_single_file_for_the_original(tmp_path,
                                                               monkeypatch):
    """#8：高级重命名 → 整个原件 1 个文件，{doc_type} 取第 1 份文档。"""
    async with AdvEnv(tmp_path, monkeypatch, action="rename",
                      naming_rule="{doc_type}_{original_name}{original_ext}",
                      review_mode="never") as env:
        txn_id = await env.seed()
        await env.run()
        files, arts, blobs = await _txn_rows(txn_id)
        root = [f for f in files if f.parent_file_id is None][0]
        assert len(arts) == 1, [(a.display_name, a.status) for a in arts]
        art = arts[0]
        assert art.file_id == root.id            # 挂在原件（根文件）上
        assert art.status == "ready"
        assert "商业发票" in art.display_name      # 第 1 份文档的值
        assert "#doc" not in art.display_name
        assert _pdf_pages(blobs[art.id]) == 7     # 整个原件


async def test_advanced_rename_waits_until_all_documents_settle(tmp_path,
                                                               monkeypatch):
    """#8/D3：有文档待复核时暂不生成，通过后才生成（修订号随之更新）。"""
    async with AdvEnv(tmp_path, monkeypatch, action="rename",
                      naming_rule="{doc_type}_{original_name}{original_ext}",
                      review_mode="auto") as env:
        env.missing_invoice = True
        txn_id = await env.seed()
        await env.run()
        files, arts, _ = await _txn_rows(txn_id)
        children = [f for f in files if f.parent_file_id]
        pending = [f for f in children if f.status == "pending_verification"]
        assert len(pending) == 1
        assert arts == [], [(a.display_name, a.status) for a in arts]

        from app.main import create_app
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://test") as c:
                r = await c.post(f"/api/v1/review/{pending[0].id}/confirm",
                                 headers={"X-User": "tester"})
                assert r.status_code == 200, r.text
        files2, arts2, _ = await _txn_rows(txn_id)
        root = [f for f in files2 if f.parent_file_id is None][0]
        assert len(arts2) == 1, [(a.display_name, a.status, a.error) for a in arts2]
        assert arts2[0].status == "ready" and arts2[0].file_id == root.id
        assert "商业发票" in arts2[0].display_name


async def test_documents_view_exposes_file_level_artifacts(tmp_path, monkeypatch):
    """D3：documents 视图的每个文件项新增文件级 artifacts 键。"""
    async with AdvEnv(tmp_path, monkeypatch, action="rename",
                      naming_rule="{doc_type}_{original_name}{original_ext}",
                      review_mode="never") as env:
        txn_id = await env.seed()
        await env.run()
        from app.main import create_app
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://test") as c:
                r = await c.get(f"/api/v1/transactions/{txn_id}/documents")
                assert r.status_code == 200, r.text
                body = r.json()
        file0 = body["files"][0]
        assert "artifacts" in file0 and len(file0["artifacts"]) == 1
        assert file0["artifacts"][0]["name"] == \
            "商业发票_25041081 CI PL 测试#1.pdf"
        # 干净值契约不变：documents 里不出现 $ 元数据
        for doc in file0["documents"]:
            assert all(not str(k).startswith("$") for k in (doc["data"] or {}))


async def test_split_artifact_follows_corrections_at_new_revision(tmp_path,
                                                                 monkeypatch):
    """#4/D3：审单修正后按新修订重新生成，命名取修正后的值。"""
    async with AdvEnv(tmp_path, monkeypatch, action="split",
                      naming_rule="{data.invoice_no}_{original_name}{original_ext}",
                      review_mode="auto") as env:
        env.missing_invoice = True            # 第 1 份文档待复核
        txn_id = await env.seed()
        await env.run()
        files, arts, _ = await _txn_rows(txn_id)
        pending = [f for f in files
                   if f.parent_file_id and f.status == "pending_verification"]
        assert len(pending) == 1
        assert arts and all(a.file_id != pending[0].id for a in arts)

        from app.main import create_app
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://test") as c:
                h = {"X-User": "tester"}
                r = await c.post(f"/api/v1/review/{pending[0].id}/lock",
                                 headers=h)
                assert r.status_code == 200, r.text
                r = await c.patch(f"/api/v1/review/{pending[0].id}/fields",
                                  headers=h,
                                  json={"edits": [{"field": "invoice_no",
                                                   "value": "INV-1001"}]})
                assert r.status_code == 200, r.text
                r = await c.post(f"/api/v1/review/{pending[0].id}/confirm",
                                 headers=h)
                assert r.status_code == 200, r.text
        files2, arts2, _ = await _txn_rows(txn_id)
        mine = [a for a in arts2 if a.file_id == pending[0].id]
        assert len(mine) == 1, [(a.display_name, a.status, a.error) for a in arts2]
        assert mine[0].status == "ready"
        assert mine[0].source_revision == 1        # 修正后修订号 +1
        assert mine[0].display_name.startswith("INV-1001_")   # 命名用修正值
