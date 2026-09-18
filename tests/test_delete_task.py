"""管理员删除任务（2026-09-18 需求：任务清单页管理员可删除任务）.

One task = one uploaded root file (+ its split children). Deleting it must:
- be admin-only (operator/viewer 403),
- remove the file rows, their blobs (original / split slice / UDR / images /
  artifacts) and the review corrections,
- leave the transaction as a tombstone so the append-only ledger keeps its
  anchor, and disappear from every task-facing read (ledger, cabinet, home
  metrics),
- survive a race with a still-running pipeline (the runner re-checks).
"""
from httpx import ASGITransport, AsyncClient

from app.models import FileArtifact, FileRecord


async def _client(tmp_path, monkeypatch, *, auth_on=True):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IDP_AUTH_MODE", "on" if auth_on else "off")
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.main import create_app
    return create_app()


async def _seed_user(email: str, role: str, tenant: str = "default"):
    from app.auth import security
    from app.db import session_factory
    from app.models import User
    sf = session_factory()
    async with sf() as s:
        u = User(tenant_id=tenant, email=email, role=role,
                 password_hash=security.hash_password("pw12345678"),
                 auth_provider="local", active=True, session_epoch=0)
        s.add(u)
        await s.commit()
        return {"id": u.id, "tenant_id": u.tenant_id, "role": u.role, "email": u.email}


def _headers(u: dict) -> dict:
    from app.auth import security
    return {"Authorization": "Bearer " + security.create_session_token(
        user_id=u["id"], tenant_id=u["tenant_id"], role=u["role"],
        email=u["email"], session_epoch=0)}


async def _seed_task(*, file_id: str, tenant: str = "default", status="completed",
                     children: tuple[str, ...] = (), with_result=True,
                     txn_status: str = "completed") -> dict:
    """A root task (optionally split) with every kind of blob on disk."""
    from app.db import session_factory
    from app.models import Correction, CreditLedger, Transaction
    from app.storage import get_storage

    st = get_storage()
    txn_id = f"t{file_id[:8]}"
    sf = session_factory()
    async with sf() as s:
        s.add(Transaction(id=txn_id, tenant_id=tenant, skill_code="del_skill",
                          skill_version=1, status=txn_status, purpose="production"))
        await s.flush()
        keys = {
            "original": st.put_bytes(f"files/{tenant}/{txn_id}/orig.pdf", b"%PDF-orig"),
            "udr": st.put_bytes(f"udr/{file_id}.json", b'{"pages": []}'),
            "images": st.put_bytes(f"udr/{file_id}.images.json", b"{}"),
            "preview": st.put_bytes(f"preview/{file_id}.pdf", b"%PDF-preview"),
            "artifact": st.put_bytes(f"artifacts/{tenant}/{file_id}/a1.pdf", b"%PDF-a1"),
        }
        s.add(FileRecord(id=file_id, tenant_id=tenant, transaction_id=txn_id,
                         file_name="bundle.pdf", storage_path=keys["original"],
                         status=status, page_count=3,
                         udr_path=keys["udr"], images_path=keys["images"],
                         result={"invoice_no": {"$value": "INV-1"}} if with_result else None))
        child_keys = {}
        for i, cid in enumerate(children):
            # a child slice of the split PDF + its own UDR/result
            child_keys[cid] = {
                "split": st.put_bytes(f"split/{cid}.pdf", b"%PDF-child"),
                "udr": st.put_bytes(f"udr/{cid}.json", b'{"pages": []}'),
            }
            s.add(FileRecord(id=cid, tenant_id=tenant, transaction_id=txn_id,
                             parent_file_id=file_id,
                             file_name=f"bundle#doc{i + 1}.pdf",
                             storage_path=child_keys[cid]["split"], status="completed",
                             page_count=2, udr_path=child_keys[cid]["udr"],
                             result={"invoice_no": {"$value": "INV-2"}}))
        await s.flush()
        s.add(FileArtifact(tenant_id=tenant, file_id=file_id, doc_index=0,
                           source_revision=0, config_hash="h", action="rename",
                           display_name="invoice.pdf",
                           storage_key=keys["artifact"], status="ready"))
        s.add(Correction(tenant_id=tenant, file_id=file_id, skill_code="del_skill",
                         skill_version=1, field="invoice_no",
                         old_value="INV-0", new_value="INV-1", reviewer="alice"))
        s.add(CreditLedger(tenant_id=tenant, kind="shadow_meter", amount=3.0,
                           transaction_id=txn_id, note="{}"))
        await s.commit()
    return {"file_id": file_id, "transaction_id": txn_id, "keys": keys,
            "child_keys": child_keys}


async def _db_state(ids: list[str]) -> dict:
    from sqlalchemy import select
    from app.db import session_factory
    from app.models import Correction, FileArtifact, Transaction
    sf = session_factory()
    async with sf() as s:
        files = (await s.execute(
            select(FileRecord.id).where(FileRecord.id.in_(ids)))).scalars().all()
        arts = (await s.execute(
            select(FileArtifact.id).where(FileArtifact.file_id.in_(ids)))).scalars().all()
        corr = (await s.execute(
            select(Correction.id).where(Correction.file_id.in_(ids)))).scalars().all()
        txns = (await s.execute(select(Transaction.id, Transaction.status))).all()
    return {"files": list(files), "artifacts": list(arts), "corrections": list(corr),
            "transactions": {t: st for t, st in txns}}


async def test_delete_task_removes_rows_blobs_and_hides_it(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            admin = await _seed_user("boss@example.com", "admin")
            task = await _seed_task(file_id="f" * 32)
            from app.storage import get_storage
            st = get_storage()

            # visible before the delete: ledger, cabinet, home metrics
            adm = _headers(admin)
            assert (await c.get("/api/v1/files", headers=adm)).json()["total"] == 1
            assert (await c.get("/api/v1/cabinet/del_skill", headers=adm)
                    ).json()["rows"]
            assert (await c.get("/api/v1/stats/home", headers=adm)
                    ).json()["passed_docs"] == 1

            r = await c.delete(f"/api/v1/files/{task['file_id']}", headers=adm)
            assert r.status_code == 200, r.text
            assert r.json() == {"file_id": task["file_id"],
                                "transaction_id": task["transaction_id"],
                                "deleted_children": 0, "deleted_blobs": 5}

            # rows gone, transaction kept as a tombstone for the ledger anchor
            state = await _db_state([task["file_id"]])
            assert state["files"] == [] and state["artifacts"] == []
            assert state["corrections"] == []
            assert state["transactions"][task["transaction_id"]] == "deleted"

            # every blob removed (original, UDR, images, preview cache, artifact)
            for key in task["keys"].values():
                assert not st.exists(key), key

            # gone from every task-facing read
            assert (await c.get("/api/v1/files", headers=adm)).json()["total"] == 0
            assert (await c.get("/api/v1/cabinet/del_skill", headers=adm)
                    ).json()["rows"] == []
            home = (await c.get("/api/v1/stats/home", headers=adm)).json()
            assert home["passed_docs"] == 0 and home["passed_pages"] == 0
            assert (await c.get(f"/api/v1/files/{task['file_id']}/artifacts",
                                headers=adm)).status_code == 404
            assert (await c.get(f"/api/v1/review/{task['file_id']}",
                                headers=adm)).status_code == 404
            assert (await c.get(f"/api/v1/files/{task['file_id']}/download",
                                headers=adm)).status_code == 404

            # the audit trail says who deleted what (the row itself is gone)
            # — and a second delete is a 404, not a silent success
            assert (await c.delete(f"/api/v1/files/{task['file_id']}",
                                   headers=adm)).status_code == 404

            from sqlalchemy import select
            from app.db import session_factory
            from app.models import AuditLog
            async with session_factory()() as s:
                logs = (await s.execute(select(AuditLog).where(
                    AuditLog.action == "files.deleted"))).scalars().all()
            assert len(logs) == 1 and logs[0].actor == "boss@example.com"
            assert logs[0].detail["file_name"] == "bundle.pdf"
            assert logs[0].detail["skill_code"] == "del_skill"
            assert logs[0].detail["page_count"] == 3


async def test_delete_task_takes_its_split_children_with_it(tmp_path, monkeypatch):
    """A split task is ONE task in the ledger; deleting the root must remove the
    child documents too (and their slices), or they stay as orphan rows."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            admin = await _seed_user("boss@example.com", "admin")
            kids = ("a" * 32, "b" * 32)
            task = await _seed_task(file_id="c" * 32, status="split", children=kids)
            from app.storage import get_storage
            st = get_storage()

            r = await c.delete(f"/api/v1/files/{task['file_id']}",
                               headers=_headers(admin))
            assert r.status_code == 200, r.text
            assert r.json()["deleted_children"] == 2

            state = await _db_state([task["file_id"], *kids])
            assert state["files"] == []
            for key in list(task["keys"].values()) + [
                    k for ck in task["child_keys"].values() for k in ck.values()]:
                assert not st.exists(key), key

            # a child is not a task: deleting one directly is a 400, not a
            # half-deleted parent
            other = await _seed_task(file_id="d" * 32, status="split",
                                     children=("e" * 32,))
            r = await c.delete("/api/v1/files/" + "e" * 32, headers=_headers(admin))
            assert r.status_code == 400
            assert (await _db_state(["e" * 32]))["files"] == ["e" * 32]
            assert other["file_id"] == "d" * 32      # untouched


async def test_delete_one_task_keeps_its_sibling_from_the_same_upload(tmp_path,
                                                                     monkeypatch):
    """One upload may carry several files — each is its own ledger row (its own
    task). Deleting one must not hide the others: the transaction only becomes a
    tombstone once its last root file is gone."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            admin = await _seed_user("boss@example.com", "admin")
            adm = _headers(admin)
            first = await _seed_task(file_id="3" * 32)
            # same upload (transaction), second root file
            from app.db import session_factory
            from app.storage import get_storage
            from app.models import Transaction
            st = get_storage()
            sibling = "4" * 32
            key = st.put_bytes("files/default/t00000000/sib.pdf", b"%PDF-sib")
            sf = session_factory()
            async with sf() as s:
                txn_id = first["transaction_id"]
                s.add(FileRecord(id=sibling, tenant_id="default",
                                 transaction_id=txn_id, file_name="sibling.pdf",
                                 storage_path=key, status="completed", page_count=1))
                await s.commit()

            assert (await c.get("/api/v1/files", headers=adm)).json()["total"] == 2
            assert (await c.delete(f"/api/v1/files/{first['file_id']}",
                                   headers=adm)).status_code == 200

            # the sibling is still a task, and the transaction is NOT tombstoned
            rows = (await c.get("/api/v1/files", headers=adm)).json()
            assert rows["total"] == 1
            assert rows["data"][0]["file_id"] == sibling
            async with sf() as s:
                assert (await s.get(Transaction, txn_id)).status == "completed"
            assert st.exists(key)                    # sibling blob untouched
            # deleting the last one tombstones the upload
            assert (await c.delete(f"/api/v1/files/{sibling}",
                                   headers=adm)).status_code == 200
            assert (await c.get("/api/v1/files", headers=adm)).json()["total"] == 0
            async with sf() as s:
                assert (await s.get(Transaction, txn_id)).status == "deleted"


async def test_delete_task_is_admin_only(tmp_path, monkeypatch):
    """The button is admin-only in the UI; the API must enforce it too."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            admin = await _seed_user("boss@example.com", "admin")
            operator = await _seed_user("op@example.com", "operator")
            viewer = await _seed_user("look@example.com", "viewer")
            task = await _seed_task(file_id="1" * 32)

            for who in (operator, viewer):
                r = await c.delete(f"/api/v1/files/{task['file_id']}",
                                   headers=_headers(who))
                assert r.status_code == 403, r.text
                assert r.json()["detail"]["code"] == "role_required"
            assert (await _db_state([task["file_id"]]))["files"] == ["1" * 32]

            # no credential at all -> 401, and cross-tenant -> 404 (never 403:
            # another tenant must not learn that the file exists)
            assert (await c.delete(f"/api/v1/files/{task['file_id']}")).status_code == 401
            await _seed_user("other@example.com", "admin", tenant="acme")
            other_admin = await _seed_user("other2@example.com", "admin", tenant="acme")
            r = await c.delete(f"/api/v1/files/{task['file_id']}",
                               headers=_headers(other_admin))
            assert r.status_code == 404
            assert (await _db_state([task["file_id"]]))["files"] == ["1" * 32]

            # the admin still can
            assert (await c.delete(f"/api/v1/files/{task['file_id']}",
                                   headers=_headers(admin))).status_code == 200


async def test_delete_missing_task_is_404(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            admin = await _seed_user("boss@example.com", "admin")
            assert (await c.delete("/api/v1/files/deadbeef",
                                   headers=_headers(admin))).status_code == 404


async def test_deleted_task_cannot_be_resurrected_by_the_runner(tmp_path, monkeypatch):
    """Deletion is allowed while the task is still queued/processing — the
    runner re-checks the tombstone instead of writing a result into a file row
    that no longer exists."""
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            admin = await _seed_user("boss@example.com", "admin")
            task = await _seed_task(file_id="2" * 32, status="processing",
                                    with_result=False, txn_status="processing")
            r = await c.delete(f"/api/v1/files/{task['file_id']}",
                               headers=_headers(admin))
            assert r.status_code == 200, r.text

            from app.db import session_factory
            from app.tasks import runner
            sf = session_factory()
            async with sf() as s:
                assert await runner._txn_deleted(s, task["file_id"]) is True
            # no file row left for the stages to grab
            assert (await _db_state([task["file_id"]]))["files"] == []
