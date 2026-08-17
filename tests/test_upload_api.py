"""Browser upload surface (2026-08-18): the contract the upload page depends on.

Covers GET /api/v1/formats (the capability list the picker reads instead of
hardcoding one) and the published_version field on the skill roster — without
it the page would happily offer a skill that /process rejects with a 400.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.skillengine.schema import FieldSpec, SkillPackage

PKG = SkillPackage(
    skill_code="upload_probe", name="上传契约测试",
    fields=[FieldSpec(name="invoice_no", instruction="发票号码")],
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _isolate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None


async def test_formats_declares_only_parsable_types(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.get("/api/v1/formats")
            assert r.status_code == 200, r.text
            body = r.json()

    exts = set(body["extensions"])
    # every type submitted end-to-end on 2026-08-18
    assert {".pdf", ".ofd", ".docx", ".xlsx", ".pptx",
            ".png", ".jpg", ".jpeg"} <= exts
    # offering these would hand the user a file that can only fail after the
    # billing freeze: legacy Office has no markitdown converter, and the
    # lite-tier OCR engine rejects bmp/webp (HTTP 400 code 1214)
    assert exts.isdisjoint({".doc", ".xls", ".ppt", ".bmp", ".webp"})
    # limits the page batches against; max_batch stays under the proxy ceiling
    assert body["max_files"] == 10
    assert body["max_size_mb"] == 50
    assert 0 < body["max_batch_mb"] < 100


async def test_skill_roster_exposes_published_version(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post("/api/v1/skills", json={"package": PKG.model_dump()})
            assert r.status_code == 201, r.text

            # draft only -> not submittable, the picker must be able to tell
            roster = (await client.get("/api/v1/skills")).json()
            row = next(s for s in roster if s["skill_code"] == "upload_probe")
            assert row["published_version"] is None

            assert (await client.post(
                "/api/v1/skills/upload_probe/versions/1/publish")).status_code == 200
            roster = (await client.get("/api/v1/skills")).json()
            row = next(s for s in roster if s["skill_code"] == "upload_probe")
            assert row["published_version"] == 1
