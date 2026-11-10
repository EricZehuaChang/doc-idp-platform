"""Document limits apply before persistence and admit the configured boundary."""
import pytest
from sqlalchemy import func, select

from app.models import FileRecord, Transaction
from tests.test_wp5_fast_and_playground import _boot


async def test_processing_file_and_batch_boundaries(tmp_path, monkeypatch):
    from app.api.routes import process

    monkeypatch.setattr(process, "_MAX_SIZE", 8)
    monkeypatch.setattr(process, "_MAX_BATCH", 12)
    submitted = []
    monkeypatch.setattr(process.runner, "submit", submitted.append)
    async for client, db in _boot(tmp_path, monkeypatch):
        package = {"skill_code": "limits", "kind": "layout", "fields": []}
        assert (await client.post("/api/v1/skills", json={"package": package})).status_code == 201
        assert (await client.post("/api/v1/skills/limits/versions/1/publish")).status_code == 200
        oversized = await client.post("/api/v1/process", data={"skill_code": "limits"},
                                      files={"files": ("large.pdf", b"x" * 9)})
        assert oversized.status_code == 413
        batch = await client.post("/api/v1/process", data={"skill_code": "limits"}, files=[
            ("files", ("a.pdf", b"x" * 7)), ("files", ("b.pdf", b"x" * 6))])
        assert batch.status_code == 413
        async with db.session_factory()() as session:
            assert await session.scalar(select(func.count()).select_from(Transaction)) == 0
            assert await session.scalar(select(func.count()).select_from(FileRecord)) == 0
        boundary = await client.post("/api/v1/process", data={"skill_code": "limits"}, files=[
            ("files", ("a.pdf", b"x" * 8)), ("files", ("b.pdf", b"x" * 4))])
        assert boundary.status_code == 202, boundary.text
        assert submitted == [boundary.json()["transaction_id"]]


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
