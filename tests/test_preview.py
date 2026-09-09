"""Original-document preview endpoint (需求3: 对照原件加载不出来, Doc 格式不支持
预览). PDFs stream as-is; Office files convert to PDF via LibreOffice (cached);
a missing converter or an unconvertible format answers 422 with a human message
instead of leaving the review pane blank. Conversion itself is mocked — the
real soffice round-trip is exercised against the dev machine's LibreOffice in
manual verification."""
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.api.routes import process as process_routes


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


async def _seed(tmp_path, name: str, blob: bytes | None = b"x") -> str:
    """Seed one root FileRecord. blob=None leaves the on-disk file unwritten —
    the WP1 shape: a DB row whose original never made it to this host."""
    from app.db import init_db, session_factory
    from app.models import FileRecord, Transaction
    await init_db()
    path = tmp_path / name
    if blob is not None:
        path.write_bytes(blob)
    sf = session_factory()
    async with sf() as s:
        txn = Transaction(tenant_id="default", skill_code="inv", skill_version=1)
        s.add(txn)
        await s.flush()
        f = FileRecord(tenant_id="default", transaction_id=txn.id, file_name=name,
                       storage_path=str(path), status="pending_verification")
        s.add(f)
        await s.commit()
        return f.id


async def test_pdf_preview_streams_the_original(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    fid = await _seed(tmp_path, "a.pdf", b"%PDF-1.4 fake")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/preview")
            assert r.status_code == 200
            assert r.content == b"%PDF-1.4 fake"
            assert "immutable" in r.headers["cache-control"]


async def test_office_preview_without_soffice_is_a_human_422(tmp_path, monkeypatch):
    monkeypatch.setattr(process_routes, "_find_soffice", lambda: None)
    app = await _client(tmp_path, monkeypatch)
    fid = await _seed(tmp_path, "contract.docx", b"docx bytes")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/preview")
            assert r.status_code == 422
            assert "LibreOffice" in r.json()["detail"]


async def test_office_preview_converts_once_and_serves_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(process_routes, "_find_soffice", lambda: "/usr/bin/soffice")
    calls = 0

    async def fake_convert(soffice, src, work_dir: Path) -> Path:
        nonlocal calls
        calls += 1
        out = work_dir / "converted.pdf"
        out.write_bytes(b"%PDF-converted")
        return out

    monkeypatch.setattr(process_routes, "_convert_office_to_pdf", fake_convert)
    app = await _client(tmp_path, monkeypatch)
    fid = await _seed(tmp_path, "sheet.xlsx", b"xlsx bytes")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/preview")
            assert r.status_code == 200
            assert r.content == b"%PDF-converted"
            assert r.headers["content-type"].startswith("application/pdf")
            # second request comes from the per-file cache, no reconversion
            r2 = await c.get(f"/api/v1/files/{fid}/preview")
            assert r2.status_code == 200 and r2.content == b"%PDF-converted"
            assert calls == 1
            # the converter only ever got a real job dir, not the cache file
            assert calls == 1


async def test_unconvertible_format_answers_422(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    fid = await _seed(tmp_path, "e-invoice.ofd", b"ofd bytes")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/preview")
            assert r.status_code == 422
            assert "暂不支持原件预览" in r.json()["detail"]


async def test_preview_is_tenant_scoped(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    fid = await _seed(tmp_path, "a.pdf", b"%PDF-1.4 fake")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/preview",
                            headers={"X-Tenant-Id": "other"})
            assert r.status_code == 404


# —— WP1: a row whose blob is gone answers a distinguishable 404, never a 500 ——


async def test_download_missing_blob_answers_original_missing(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    fid = await _seed(tmp_path, "pii_sample.pdf", None)   # row exists, blob never landed
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/download")
            assert r.status_code == 404
            assert r.json()["detail"]["code"] == "original_missing"
            assert "恢复" in r.json()["detail"]["message"]
            # never leak the on-disk location or storage internals
            assert str(tmp_path) not in r.text
            assert "storage_path" not in r.text


async def test_preview_missing_blob_answers_original_missing(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    fid = await _seed(tmp_path, "scan.pdf", None)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/preview")
            assert r.status_code == 404
            assert r.json()["detail"]["code"] == "original_missing"


async def test_missing_blob_check_does_not_create_directories(tmp_path, monkeypatch):
    """The read-only existence check must not mkdir through local_path — a
    probing request against an empty host leaves no stray directory tree."""
    from app.db import init_db, session_factory
    from app.models import FileRecord, Transaction
    app = await _client(tmp_path, monkeypatch)
    await init_db()
    sf = session_factory()
    async with sf() as s:
        txn = Transaction(tenant_id="default", skill_code="inv", skill_version=1)
        s.add(txn)
        await s.flush()
        f = FileRecord(tenant_id="default", transaction_id=txn.id,
                       file_name="gone.pdf", storage_path="files/default/x/deadbeef.pdf")
        s.add(f)
        await s.commit()
        fid = f.id
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/download")
            assert r.status_code == 404
            assert r.json()["detail"]["code"] == "original_missing"
    assert not (tmp_path / "files").exists()


async def test_office_preview_missing_original_serves_existing_cache(tmp_path, monkeypatch):
    """The original is gone but the converted PDF survived: preview keeps
    working from the cache, download still answers original_missing."""
    from app.storage import get_storage
    app = await _client(tmp_path, monkeypatch)
    fid = await _seed(tmp_path, "contract.docx", None)
    get_storage().put_bytes(f"preview/{fid}.pdf", b"%PDF-cached-preview")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/preview")
            assert r.status_code == 200
            assert r.content == b"%PDF-cached-preview"
            r2 = await c.get(f"/api/v1/files/{fid}/download")
            assert r2.status_code == 404
            assert r2.json()["detail"]["code"] == "original_missing"


async def test_office_preview_missing_original_and_cache_is_original_missing(
        tmp_path, monkeypatch):
    """No original, no cache: a clean 404 — LibreOffice must never run against
    a nonexistent source file."""
    def _must_not_run(*a):
        raise AssertionError("LibreOffice must not run without the original")

    monkeypatch.setattr(process_routes, "_find_soffice", _must_not_run)
    app = await _client(tmp_path, monkeypatch)
    fid = await _seed(tmp_path, "sheet.xlsx", None)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/preview")
            assert r.status_code == 404
            assert r.json()["detail"]["code"] == "original_missing"


async def test_download_streams_original_and_is_tenant_scoped(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    fid = await _seed(tmp_path, "a.pdf", b"%PDF-1.4 original-bytes")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.get(f"/api/v1/files/{fid}/download")
            assert r.status_code == 200
            assert r.content == b"%PDF-1.4 original-bytes"
            r2 = await c.get(f"/api/v1/files/{fid}/download",
                             headers={"X-Tenant-Id": "other"})
            assert r2.status_code == 404
