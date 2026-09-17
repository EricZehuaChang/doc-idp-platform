"""9.15 WP6 (R13): artifact listing + download (§3.6).

Security gates: storage keys are id-built (no path traversal), display names
only appear in the response header (encoded, CRLF-stripped), cross-tenant is
404, agent keys may only download artifacts of their own transactions.
"""
from __future__ import annotations

from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.http_headers import content_disposition
from app.db import session_factory
from app.models import FileArtifact, FileRecord, Transaction
from app.storage import get_storage
from app.tenancy import current_actor, current_tenant

router = APIRouter(prefix="/api/v1", tags=["artifacts"])


def _view(a: FileArtifact) -> dict:
    return {"artifact_id": a.id, "file_id": a.file_id,
            "doc_index": a.doc_index, "action": a.action,
            "name": a.display_name, "status": a.status,
            "error": a.error, "size": a.size,
            "sha256": a.sha256, "searchable": a.searchable,
            "created_at": a.created_at.isoformat()}


async def _list_for(s, file_ids: list[str], tenant: str) -> list[FileArtifact]:
    if not file_ids:
        return []
    return list((await s.execute(
        select(FileArtifact)
        .where(FileArtifact.tenant_id == tenant,
               FileArtifact.file_id.in_(file_ids),
               FileArtifact.status != "pending")   # superseded rows stay hidden
        .order_by(FileArtifact.created_at, FileArtifact.id))).scalars().all())


def _guard_agent(txn: Transaction | None) -> None:
    """Agent keys: production txns only, and only ones they created (§3.6)."""
    actor = current_actor()
    if actor.get("key_type") != "agent":
        return
    if txn is None or txn.purpose == "test" or txn.initiator_id != actor.get("name"):
        raise HTTPException(404, "artifact not found")


@router.get("/files/{file_id}/artifacts")
async def file_artifacts(file_id: str):
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        if f is None or f.tenant_id != tenant:
            raise HTTPException(404, "file not found")
        txn = await s.get(Transaction, f.transaction_id)
        _guard_agent(txn)
        rows = await _list_for(s, [file_id], tenant)
    return {"artifacts": [_view(a) for a in rows]}


@router.get("/transactions/{txn_id}/artifacts")
async def txn_artifacts(txn_id: str):
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        txn = await s.get(Transaction, txn_id)
        if txn is None or txn.tenant_id != tenant:
            raise HTTPException(404, "transaction not found")
        if txn.purpose == "test" and current_actor().get("key_type") == "agent":
            raise HTTPException(404, "transaction not found")
        files = (await s.execute(
            select(FileRecord.id)
            .where(FileRecord.transaction_id == txn_id))).scalars().all()
        rows = await _list_for(s, list(files), tenant)
    return {"artifacts": [_view(a) for a in rows]}


@router.get("/artifacts/{artifact_id}/download")
async def download(artifact_id: str):
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        a = await s.get(FileArtifact, artifact_id)
        if a is None or a.tenant_id != tenant:
            raise HTTPException(404, "artifact not found")
        f = await s.get(FileRecord, a.file_id)
        txn = await s.get(Transaction, f.transaction_id) if f else None
        _guard_agent(txn)
        if a.status == "pending":
            raise HTTPException(409, detail={"code": "artifact_not_ready",
                                             "message": "产出文件尚未就绪"})
        if a.status != "ready" or not a.storage_key:
            raise HTTPException(404, "artifact not available")
        key, name = a.storage_key, a.display_name

    st = get_storage()

    async def _stream() -> AsyncIterator[bytes]:
        yield st.read_bytes(key)

    from mimetypes import guess_type
    media = guess_type(name)[0] or "application/octet-stream"
    return StreamingResponse(_stream(), media_type=media, headers={
        "Content-Disposition": content_disposition(name),
        "Cache-Control": "no-store"})
