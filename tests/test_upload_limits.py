"""Document limits apply before persistence and admit the configured boundary."""
import pytest
from sqlalchemy import func, select

from app.models import FileRecord, Transaction
from tests.test_wp5_fast_and_playground import _boot


async def test_processing_file_and_batch_boundaries(tmp_path, monkeypatch):
    from starlette.datastructures import UploadFile
    from app.api.routes import process

    monkeypatch.setattr(process, "_MAX_SIZE", 8)
    monkeypatch.setattr(process, "_MAX_BATCH", 12)
    submitted = []
    reads = []
    original_read = UploadFile.read

    async def tracked_read(upload, size=-1):
        reads.append((upload.filename, size))
        return await original_read(upload, size)

    monkeypatch.setattr(UploadFile, "read", tracked_read)
    monkeypatch.setattr(process.runner, "submit", submitted.append)
    async for client, db in _boot(tmp_path, monkeypatch):
        package = {"skill_code": "limits", "kind": "layout", "fields": []}
        assert (await client.post("/api/v1/skills", json={"package": package})).status_code == 201
        assert (await client.post("/api/v1/skills/limits/versions/1/publish")).status_code == 200
        oversized = await client.post("/api/v1/process", data={"skill_code": "limits"},
                                      files={"files": ("large.pdf", b"x" * 9)})
        assert oversized.status_code == 413
        assert reads == []
        batch = await client.post("/api/v1/process", data={"skill_code": "limits"}, files=[
            ("files", ("a.pdf", b"x" * 7)), ("files", ("b.pdf", b"x" * 6))])
        assert batch.status_code == 413
        assert reads == [("a.pdf", 9)]
        reads.clear()
        async with db.session_factory()() as session:
            assert await session.scalar(select(func.count()).select_from(Transaction)) == 0
            assert await session.scalar(select(func.count()).select_from(FileRecord)) == 0
        boundary = await client.post("/api/v1/process", data={"skill_code": "limits"}, files=[
            ("files", ("a.pdf", b"x" * 8)), ("files", ("b.pdf", b"x" * 4))])
        assert boundary.status_code == 202, boundary.text
        assert submitted == [boundary.json()["transaction_id"]]
        assert reads == [("a.pdf", 9), ("b.pdf", 5)]


@pytest.mark.parametrize("endpoint,form", [
    ("/api/v1/studio/samples", {}),
    ("/api/v1/detect", {"kinds": "seal"}),
    ("/api/v1/locate", {"terms": '["term"]'}),
    ("/api/v1/skills/limits/golden", {"expected": "{}"}),
    ("/api/v1/skills/dry-run", {"package": '{"skill_code":"limits","kind":"layout"}'}),
])
async def test_tools_and_samples_reject_oversized_documents_before_processing(
        tmp_path, monkeypatch, endpoint, form):
    from app.api.routes import detect, locate, skills, studio

    for module in (detect, locate, studio):
        monkeypatch.setattr(module, "_MAX_SIZE", 8)
    monkeypatch.setattr(skills, "MAX_FILE_SIZE", 8)
    monkeypatch.setattr(detect, "_make_detector", lambda name: object())
    async for client, _ in _boot(tmp_path, monkeypatch):
        result = await client.post(endpoint, data=form,
                                   files={"file": ("large.pdf", b"x" * 9, "application/pdf")})
        assert result.status_code == 413, result.text


async def test_agent_advertises_200_mib(tmp_path, monkeypatch):
    async for client, _ in _boot(tmp_path, monkeypatch):
        response = await client.get("/api/v1/agent/ping")
        assert response.status_code == 200
        assert response.json()["upload_max_mb"] == 200


async def test_rejected_tool_call_logs_original_file_size(tmp_path, monkeypatch):
    from app.api.routes import detect, locate
    from app.models import ApiCallLog

    monkeypatch.setattr(detect, "_MAX_SIZE", 8)
    monkeypatch.setattr(locate, "_MAX_SIZE", 8)
    monkeypatch.setattr(detect, "_make_detector", lambda name: object())
    async for client, db in _boot(tmp_path, monkeypatch, auth_on=True):
        login = await client.post("/api/v1/auth/login", json={
            "email": "admin@example.com", "password": "admin-pass-123"})
        admin = {"Authorization": "Bearer " + login.json()["access_token"]}
        key = await client.post("/api/v1/settings/api-keys", headers=admin,
                                json={"name": "size-audit", "key_type": "application"})
        headers = {"Authorization": "Bearer " + key.json()["api_key"]}
        for endpoint, form in [("detect", {"kinds": "seal"}),
                               ("locate", {"terms": '["term"]'})]:
            response = await client.post(f"/api/v1/{endpoint}", headers=headers, data=form,
                                          files={"file": ("large.pdf", b"x" * 16)})
            assert response.status_code == 413
        async with db.session_factory()() as session:
            logs = list(await session.scalars(select(ApiCallLog)))
            assert len(logs) == 2 and all(row.size_bytes == 16 for row in logs)
