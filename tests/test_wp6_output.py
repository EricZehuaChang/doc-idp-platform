"""9.15 WP6: naming rules (table-driven, R12), output_stage artifacts (R13),
download authorization, searchable-PDF text layer (pdfplumber acceptance)."""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.extraction import naming
from app.models import FileArtifact, FileRecord, Skill, SkillVersion, Transaction


# ---------------------------------------------------------------- naming (R12)


@pytest.mark.parametrize("pattern,ctx,expected", [
    # every variable
    ("{original_name}", dict(original_name="报告.pdf", original_ext=""), "报告"),
    ("{date}_{time}",
     dict(completed_at=__import__("datetime").datetime(
         2026, 9, 17, 8, 9, 10, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))),
     "20260917_080910.pdf"),
    ("{doc_index}", dict(doc_index=2), "02.pdf"),
    ("{doc_index}", dict(doc_index=100), "100.pdf"),
    ("{doc_type}_{original_name}", dict(doc_type="发票", original_name="a.pdf",
                                        original_ext=""), "发票_a"),
    ("{data.invoice_no}{original_ext}", dict(data={"invoice_no": "INV-9"}),
     "INV-9.pdf"),
    # cleaning rules
    ("{original_name}", dict(original_name="a*b?c.pdf", original_ext=""), "abc"),
    ("{original_name}", dict(original_name="报告  最终.pdf", original_ext=""),
     "报告 最终"),
    ("{original_name}", dict(original_name="name..pdf", original_ext=""), "name"),
    # windows reserved names
    ("{original_name}", dict(original_name="CON.pdf", original_ext=""), "_CON"),
    ("{original_name}", dict(original_name="lpt2.txt", original_ext=""), "_lpt2"),
    # byte truncation keeps the extension and never splits a character
    ("{original_name}{original_ext}",
     dict(original_name="名" * 120 + ".pdf"), "名" * 58 + ".pdf"),
])
def test_naming_table(pattern, ctx, expected):
    name, errs = naming.render(pattern, original_name=ctx.get("original_name", ""),
                               original_ext=ctx.get("original_ext", ".pdf"),
                               output_is_pdf=ctx.get("output_is_pdf", False),
                               doc_type=ctx.get("doc_type"),
                               doc_index=ctx.get("doc_index"),
                               data=ctx.get("data"),
                               completed_at=ctx.get("completed_at"))
    assert errs == []
    assert name == expected


def test_naming_empty_main_falls_back():
    # original_name ".pdf" -> main empty; auto ext -> document_1.pdf
    name, errs = naming.render("{original_name}", original_name=".pdf",
                               original_ext=".pdf", output_is_pdf=False)
    assert errs == [] and name == "document_1.pdf"


def test_naming_output_ext_is_output_extension():
    # PDF output (searchable or pdf slice) wins over the original ext case
    name, _ = naming.render("{original_name}{original_ext}",
                            original_name="a.PDF", original_ext=".PDF",
                            output_is_pdf=True)
    assert name.endswith(".pdf")
    # non-pdf split artifact keeps the (lowercased) original extension
    name, _ = naming.render("{doc_index}{original_ext}", original_name="a.PNG",
                            original_ext=".PNG", output_is_pdf=False,
                            doc_index=1)
    assert name == "01.png"


def test_naming_unknown_variable_and_escapes():
    errs = naming.validate_pattern("{bogus} {date}")
    assert errs and errs[0]["token"] == "{bogus}"
    name, errs = naming.render("{{x}}_{date}{original_ext}",
                               original_name="a.pdf", original_ext=".pdf",
                               output_is_pdf=False,
                               completed_at=__import__("datetime").datetime(
                                   2026, 9, 17,
                                   tzinfo=__import__("zoneinfo").ZoneInfo(
                                       "Asia/Shanghai")))
    assert errs == [] and name == "{x}_20260917.pdf"
    assert naming.tokens_of("{doc_type}_{data.no}_{date}") == \
        ["doc_type", "data.no", "date"]


def test_naming_auto_appends_extension():
    p, appended = naming.ensure_extension("{original_name}")
    assert appended and p == "{original_name}{original_ext}"
    p, appended = naming.ensure_extension("{original_name}{original_ext}")
    assert not appended


# ------------------------------------------------------------ output stage (R13)


def _std_pkg(code: str, action: str, rule: str = "",
             searchable: bool = False) -> dict:
    return {"skill_code": code, "name": code, "kind": "extract",
            "schema_version": 2,
            "fields": [{"name": "invoice_no", "type": "string",
                        "instruction": "号码", "mode": "verbatim"}],
            "validators": [],
            "review_policy": {"mode": "auto", "confidence_threshold": 2},
            "model_binding": {"extractor": "", "fallback": None,
                              "challenger": None},
            "output": {"enabled": action != "off", "action": action,
                       "naming_rule": rule, "searchable_pdf": searchable}}


def _write_pdf(path, pages=1, text="发票号码 INV-A\n合计 100"):
    """Tiny real PDF via pdfplumber-compatible writer (pypdf)."""
    from io import BytesIO
    from reportlab.pdfgen import canvas
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    for i in range(pages):
        c.setFont("Helvetica", 12)
        c.drawString(72, 800, text if pages == 1 else f"page {i + 1}")
        c.showPage()
    c.save()
    path.write_bytes(buf.getvalue())


async def _boot_skill(tmp_path, monkeypatch, pkg, pdf_pages=1):
    import tests.test_advanced_runner as adv

    async with adv.Env(tmp_path, monkeypatch, adv.adv_pkg()) as env:
        from app.db import session_factory
        from app.storage import get_storage
        async with session_factory()() as s:
            s.add(Skill(code="art", tenant_id="default", name="a", kind="extract"))
            s.add(SkillVersion(tenant_id="default", skill_code="art", version=1,
                               status="published", package=pkg))
            txn = Transaction(tenant_id="default", skill_code="art",
                              skill_version=1)
            s.add(txn)
            await s.flush()
            st = get_storage()
            pdf = tmp_path / "src.pdf"
            _write_pdf(pdf, pdf_pages)
            key = st.put_bytes(f"files/default/{txn.id}/src.pdf",
                               pdf.read_bytes())
            s.add(FileRecord(tenant_id="default", transaction_id=txn.id,
                             file_name="src.pdf", storage_path=key,
                             page_count=pdf_pages, status="completed",
                             result={"invoice_no": {"$value": "INV-A",
                                                    "$confidence": 3}}))
            await s.commit()
        yield env


async def test_output_rename_generates_ready_artifact(tmp_path, monkeypatch):
    from app.db import session_factory
    from app.storage import get_storage
    async for env in _boot_skill(tmp_path, monkeypatch,
                                 _std_pkg("art", "rename",
                                          rule="{original_name}_{doc_type}")):
        from app.tasks.output_stage import output_stage
        async with session_factory()() as s:
            f = (await s.execute(select(FileRecord))).scalars().first()
            fid = f.id
        await output_stage(fid)
        async with session_factory()() as s:
            arts = (await s.execute(select(FileArtifact))).scalars().all()
            assert len(arts) == 1 and arts[0].status == "ready"
            # {doc_type} empty in standard mode; auto ext; content = original
            assert arts[0].display_name == "src_.pdf"
            assert arts[0].size > 0 and arts[0].sha256
            st = get_storage()
            blob = st.read_bytes(arts[0].storage_key)
            assert blob.startswith(b"%PDF")


async def test_output_failure_isolation(tmp_path, monkeypatch):
    """A broken rule (unknown var) must not touch the file's recognition
    status; the artifact stage simply produces nothing."""
    from app.db import session_factory
    from app.models import FileRecord
    async for env in _boot_skill(tmp_path, monkeypatch,
                                 _std_pkg("art", "rename", rule="{bogus}.pdf")):
        from app.tasks.output_stage import output_stage
        async with session_factory()() as s:
            f = (await s.execute(select(FileRecord))).scalars().first()
            await s.commit()
            fid = f.id
        await output_stage(fid)
        async with session_factory()() as s:
            f = await s.get(FileRecord, fid)
            assert f.status == "completed"          # recognition state intact
            arts = (await s.execute(select(FileArtifact))).scalars().all()
            assert arts == []


async def test_searchable_pdf_text_layer(tmp_path, monkeypatch):
    """Acceptance (§WP6): pdfplumber extracts the field value from the
    generated searchable PDF of a scanned (image) source; page size matches."""
    from app.db import session_factory

    async for env in _boot_skill(tmp_path, monkeypatch,
                                 _std_pkg("art", "rename",
                                          searchable=True)):
        from app.tasks.output_stage import output_stage
        from app.storage import get_storage
        async with session_factory()() as s:
            f = (await s.execute(select(FileRecord))).scalars().first()
            fid = f.id
        # swap the original for a scanned (image-only, no text layer) PDF
        import io as _io

        from reportlab.pdfgen import canvas as rl_canvas
        async with session_factory()() as s:
            f = await s.get(FileRecord, fid)
            st = get_storage()
            src = st.local_path(f.storage_path)
            import pypdfium2
            pdf = pypdfium2.PdfDocument(src)
            bmp = pdf[0].render(scale=2.0)
            pil = bmp.to_pil()
            pdf.close()
            img_path = tmp_path / "scan-page.png"
            pil.save(img_path, format="PNG")
            # a scanned PDF: page image, zero text operators
            scan_buf = _io.BytesIO()
            c = rl_canvas.Canvas(scan_buf, pagesize=(595, 842))
            c.drawImage(str(img_path), 0, 0, width=595, height=842)
            c.showPage()
            c.save()
            key2 = st.put_bytes(f"scan/{fid}.pdf", scan_buf.getvalue())
            f.storage_path = key2
            from app.parsers.base import Block, Page, UDR
            # raster-parser UDR: pixel coordinates on the rendered page
            udr = UDR(pages=[Page(page_no=1, width=pil.width, height=pil.height,
                                  blocks=[Block(text="发票号码 INV-A 合计 100",
                                                bbox=[100, 100, 700, 130])])],
                      full_markdown="x", parser="ocr")
            f.udr_path = st.put_bytes(f"udr/{fid}.json",
                                      udr.model_dump_json().encode())
            await s.commit()
        await output_stage(fid)
        async with session_factory()() as s:
            a = (await s.execute(select(FileArtifact))).scalars().first()
            assert a is not None and a.status == "ready"
            assert a.searchable is True
            st = get_storage()
            blob = st.read_bytes(a.storage_key)
            import pdfplumber
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
                fh.write(blob)
                fh.flush()
                with pdfplumber.open(fh.name) as pdf:
                    text = (pdf.pages[0].extract_text() or "")
                    w, h = pdf.pages[0].width, pdf.pages[0].height
            assert "INV-A" in text.replace(" ", "")
            assert abs(w - 595) < 2 and abs(h - 842) < 2   # page size kept


async def test_download_authorization(tmp_path, monkeypatch):
    from app.db import session_factory

    async for env in _boot_skill(tmp_path, monkeypatch,
                                 _std_pkg("art", "rename")):
        from app.main import create_app
        from app.tasks.output_stage import output_stage
        async with session_factory()() as s:
            f = (await s.execute(select(FileRecord))).scalars().first()
            f.result = {"invoice_no": {"$value": "V", "$confidence": 3}}
            await s.commit()
            fid = f.id
        await output_stage(fid)
        async with session_factory()() as s:
            a = (await s.execute(select(FileArtifact))).scalars().first()
            aid, name = a.id, a.display_name
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://test") as c:
                r = await c.get(f"/api/v1/artifacts/{aid}/download")
                assert r.status_code == 200
                cd = r.headers["content-disposition"]
                assert f'filename*={name}' in cd or name in cd
                assert "\r" not in cd and "\n" not in cd
                # 409 on a pending artifact (fresh row, same key shape)
                async with session_factory()() as s:
                    row = FileArtifact(tenant_id="default", file_id=fid,
                                       doc_index=None, source_revision=99,
                                       config_hash="x", action="rename",
                                       display_name="p.pdf", status="pending")
                    s.add(row)
                    await s.commit()
                    pid = row.id
                r = await c.get(f"/api/v1/artifacts/{pid}/download")
                assert r.status_code == 409
                assert r.json()["detail"]["code"] == "artifact_not_ready"
                r = await c.get("/api/v1/artifacts/missing/download")
                assert r.status_code == 404


async def test_revision_replaces_old_artifacts(tmp_path, monkeypatch):
    from app.db import session_factory

    async for env in _boot_skill(tmp_path, monkeypatch,
                                 _std_pkg("art", "rename",
                                          rule="{data.invoice_no}{original_ext}")):
        from app.tasks.output_stage import output_stage
        async with session_factory()() as s:
            f = (await s.execute(select(FileRecord))).scalars().first()
            await s.commit()
            fid = f.id
        await output_stage(fid)
        async with session_factory()() as s:
            f = await s.get(FileRecord, fid)
            f.result_revision = 1
            f.result = {"invoice_no": {"$value": "V2", "$confidence": 3}}
            await s.commit()
        await output_stage(fid)
        async with session_factory()() as s:
            arts = (await s.execute(select(FileArtifact)
                                    .order_by(FileArtifact.source_revision))).scalars().all()
            # only the newest revision's artifact remains, named with V2
            assert len(arts) == 1 and arts[0].source_revision == 1
            assert arts[0].display_name == "V2.pdf"


async def test_put_warns_when_download_normalized_off(tmp_path, monkeypatch):
    """Download on but no action -> server flips it off and reports warnings[]."""
    from tests.test_advanced_runner import Env, adv_pkg

    pkg = adv_pkg(code="art")
    pkg.output.enabled = True
    pkg.output.action = "off"          # the normalization trigger
    async with Env(tmp_path, monkeypatch, pkg):
        from app.main import create_app
        app = create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://test") as c:
                body = pkg.model_dump()
                r = await c.post("/api/v1/skills", json={"package": body,
                                                         "changelog": "x"})
                assert r.status_code == 201, r.text
                r = await c.put("/api/v1/skills/art/versions/1",
                                json={"package": body, "changelog": "x"})
                assert r.status_code == 200
                assert "下载开关已恢复为关闭" in r.json()["warnings"][0]
