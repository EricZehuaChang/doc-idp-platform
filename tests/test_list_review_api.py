"""Record-scoped review writes preserve the original lock, audit and tenant boundaries."""
import asyncio
import json

import pytest
from sqlalchemy import select

from app.models import Correction, FileRecord, Transaction
from tests.test_delete_task import _headers, _seed_user
from tests.test_list_result_views import receipts
from tests.test_wp5_fast_and_playground import _boot

URL = "/api/v1/review/receipt-scan"


@pytest.fixture
async def review_case(tmp_path, monkeypatch):
    async for client, db in _boot(tmp_path, monkeypatch, auth_on=True):
        owner = await _seed_user("reviewer@example.com", "operator")
        async with db.session_factory()() as session:
            txn = Transaction(id="receipts-txn", tenant_id="default", skill_code="receipts",
                              skill_version=1, initiator_user_id=owner["id"],
                              status="pending_verification", execution_snapshot={
                                  "package": {"output_shape": "list", "fields": [
                                      {"name": "receipt", "type": "string"},
                                      {"name": "items", "type": "table",
                                       "columns": [{"name": "name", "type": "string"}]},
                                  ]}})
            session.add(txn)
            await session.flush()
            session.add(FileRecord(id="receipt-scan", transaction_id=txn.id,
                                   tenant_id="default", storage_path="unused", file_name="receipts.png",
                                   status="pending_verification", page_count=1, result=receipts(),
                                   document_meta={"output_shape": "list",
                                                  "review_reasons": ["records_overlap"]}))
            await session.commit()
        client.headers.update(_headers(owner))
        yield client, db


def patch_body(record, field, value, revision):
    return {"expected_revision": revision, "edits": [{"record": record, "field": field, "value": value}]}


async def test_edit_delete_list_record_are_scoped_and_audited(review_case):
    client, db = review_case
    detail = await client.get(URL)
    assert detail.status_code == 200, detail.text
    assert (detail.json()["output_shape"], detail.json()["result_revision"]) == ("list", 0)
    assert detail.json()["review_reasons"] == ["records_overlap"]
    edit = await client.patch(URL + "/fields", json=patch_body(1, "receipt", "B", 0))
    assert edit.status_code == 423
    assert (await client.delete(URL + "/records/0?expected_revision=0")).status_code == 423
    assert (await client.post(URL + "/lock")).status_code == 200

    edit = await client.patch(URL + "/fields", json={
        "expected_revision": 0, "edits": [
            {"record": 1, "field": "receipt", "value": "B"},
            {"record": 1, "field": "items", "rows": [{"name": "updated", "quantity": "2"}]},
        ]})
    assert edit.status_code == 200, edit.text
    after = (await client.get(URL)).json()
    assert after["result_revision"] == 1
    rows = after["result"]["records"]
    assert rows[0] == receipts()["records"][0]
    assert rows[1]["receipt"]["$value"] == "B"
    assert rows[1]["receipt"]["$confidence"] == 3
    assert rows[1]["items"] == [{"name": "updated", "quantity": "2"}]

    assert (await client.delete(URL + "/records/0?expected_revision=0")).status_code == 409
    removed = await client.delete(URL + "/records/0?expected_revision=1")
    assert removed.status_code == 200, removed.text
    # A repeated positional deletion must never remove the next record.
    assert (await client.delete(URL + "/records/0?expected_revision=1")).status_code == 409
    final = (await client.get(URL)).json()
    assert final["result_revision"] == 2
    assert final["result"]["records"] == [rows[1]]
    async with db.session_factory()() as session:
        history = {row.field: row for row in (await session.scalars(select(Correction))).all()}
        assert set(history) == {"records[1].receipt", "records[1].items", "records[0]"}
        assert history["records[1].receipt"].old_value == "R-002"
        assert history["records[1].receipt"].new_value == "B"
        assert history["records[1].items"].reviewer == "reviewer@example.com"
        assert json.loads(history["records[0]"].old_value) == receipts()["records"][0]


async def test_list_review_rejects_invalid_indexes_and_rolls_back_the_batch(review_case):
    client, db = review_case
    assert (await client.post(URL + "/lock")).status_code == 200
    assert (await client.patch(URL + "/fields", json={
        "edits": [{"record": 0, "field": "receipt", "value": "no revision"}]})).status_code == 400
    assert (await client.delete(URL + "/records/1")).status_code == 400
    metadata = {"record": 0, "field": "$record_bbox", "rows": [{"fake": 1}]}
    assert (await client.patch(URL + "/fields", json={
        "expected_revision": 0, "edits": [metadata]})).status_code == 400
    for index in (-1, 2):
        body = patch_body(0, "receipt", "must roll back", 0)
        body["edits"].append({"record": index, "field": "receipt", "value": "outside"})
        assert (await client.patch(URL + "/fields", json=body)).status_code == 400
        assert (await client.delete(URL + f"/records/{index}?expected_revision=0")).status_code == 400
    detail = (await client.get(URL)).json()
    assert detail["result"] == receipts() and detail["result_revision"] == 0
    async with db.session_factory()() as session:
        assert not (await session.scalars(select(Correction))).all()


async def test_concurrent_edits_with_one_revision_apply_only_once(review_case):
    client, db = review_case
    assert (await client.post(URL + "/lock")).status_code == 200
    outcomes = await asyncio.gather(*[
        client.patch(URL + "/fields", json=patch_body(0, "receipt", value, 0))
        for value in ("window-1", "window-2")
    ])
    assert sorted(r.status_code for r in outcomes) == [200, 409]
    stored = (await client.get(URL)).json()
    assert stored["result_revision"] == 1
    async with db.session_factory()() as session:
        correction = (await session.scalars(select(Correction))).one()
        assert correction.new_value == stored["result"]["records"][0]["receipt"]["$value"]


async def test_record_mutation_enforces_file_access_behind_the_lock(review_case):
    client, _ = review_case
    assert (await client.post(URL + "/lock")).status_code == 200
    for role, tenant, status_code in [("tenant_admin", "default", 404),
                                      ("admin", "other", 404), ("viewer", "default", 403)]:
        user = await _seed_user(f"{role}@example.com", role, tenant)
        headers = _headers(user)
        assert (await client.patch(URL + "/fields", headers=headers,
                                   json=patch_body(0, "receipt", "blocked", 0))).status_code == status_code
        assert (await client.delete(URL + "/records/0?expected_revision=0",
                                    headers=headers)).status_code == status_code
        assert (await client.post(URL + "/records", headers=headers,
                                  json={"expected_revision": 0})).status_code == status_code
    detail = (await client.get(URL)).json()
    assert detail["result"] == receipts() and detail["result_revision"] == 0

@pytest.mark.parametrize("start_empty", [False, True])
async def test_append_record_uses_frozen_shape_and_preserves_existing_rows(review_case, start_empty):
    client, db = review_case
    original = [] if start_empty else receipts()["records"]
    if start_empty:
        async with db.session_factory()() as session:
            file = await session.get(FileRecord, "receipt-scan")
            file.result = {"records": []}
            await session.commit()
    added = await client.post(URL + "/records", json={"expected_revision": 0})
    assert added.status_code == 423
    assert (await client.post(URL + "/lock")).status_code == 200
    added = await client.post(URL + "/records", json={"expected_revision": 0})
    assert added.status_code in (200, 201), added.text
    index = len(original)
    assert added.json()["record_index"] == index
    assert (await client.post(URL + "/records", json={"expected_revision": 0})).status_code == 409
    detail = (await client.get(URL)).json()
    rows = detail["result"]["records"]
    assert rows[:index] == original
    assert len(rows) == index + 1
    assert rows[index]["receipt"]["$value"] == "" and rows[index]["items"] == []
    assert detail["result_revision"] == 1
    async with db.session_factory()() as session:
        correction = (await session.scalars(select(Correction))).one()
        assert correction.field == f"records[{index}]"


@pytest.mark.parametrize("intervening", ["approve", "unlock", "unlock-relock", "expired"])
async def test_interleaved_lock_or_decision_invalidates_stale_write(review_case, monkeypatch, intervening):
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from app.api.routes import review
    from app.integrations import webhooks
    from app.tasks import output_stage

    client, db = review_case
    assert (await client.post(URL + "/lock")).status_code == 200
    monkeypatch.setattr(webhooks, "fire", AsyncMock())
    monkeypatch.setattr(output_stage, "output_stage", AsyncMock())
    checked, resume = asyncio.Event(), asyncio.Event()
    real_save = review._save_list_result

    async def pause_before_update(*args):
        checked.set()
        await resume.wait()
        await real_save(*args)

    monkeypatch.setattr(review, "_save_list_result", pause_before_update)
    edit = asyncio.create_task(client.patch(URL + "/fields", json=patch_body(0, "receipt", "stale", 0)))
    try:
        await asyncio.wait_for(checked.wait(), 5)
        if intervening == "approve":
            assert (await client.post(URL + "/confirm")).status_code == 200
        elif intervening == "expired":
            later = review._now() + timedelta(minutes=16)
            monkeypatch.setattr(review, "_now", lambda: later)
        else:
            assert (await client.post(URL + "/unlock")).status_code == 200
            if intervening == "unlock-relock":
                assert (await client.post(URL + "/lock")).status_code == 200
    finally:
        resume.set()
    response = await asyncio.wait_for(edit, 5)
    assert response.status_code == 409, response.text
    async with db.session_factory()() as session:
        file = await session.get(FileRecord, "receipt-scan")
        assert file.result == receipts() and not file.result_revision
        assert not (await session.scalars(select(Correction))).all()


async def test_concurrent_edit_invalidates_decision_that_saw_the_old_revision(review_case, monkeypatch):
    from app.api.routes import review

    client, db = review_case
    assert (await client.post(URL + "/lock")).status_code == 200
    checked, resume = asyncio.Event(), asyncio.Event()
    real_decide = review._finalize_list_review

    async def pause_before_decision(*args):
        checked.set()
        await resume.wait()
        await real_decide(*args)

    monkeypatch.setattr(review, "_finalize_list_review", pause_before_decision)
    decision = asyncio.create_task(client.post(URL + "/reject"))
    try:
        await asyncio.wait_for(checked.wait(), 5)
        edit = await client.patch(URL + "/fields", json=patch_body(0, "receipt", "updated", 0))
        assert edit.status_code == 200, edit.text
    finally:
        resume.set()
    response = await asyncio.wait_for(decision, 5)
    assert response.status_code == 409, response.text
    async with db.session_factory()() as session:
        file = await session.get(FileRecord, "receipt-scan")
        assert file.status == "pending_verification" and file.result_revision == 1
        assert file.result["records"][0]["receipt"]["$value"] == "updated"


async def test_legacy_list_job_uses_pinned_version_across_read_write_and_export(review_case):
    from app.models import Skill, SkillVersion

    client, db = review_case
    async with db.session_factory()() as session:
        txn = await session.get(Transaction, "receipts-txn")
        package = txn.execution_snapshot["package"]
        session.add(Skill(code="receipts", name="legacy", tenant_id="default"))
        await session.flush()
        session.add(SkillVersion(skill_code="receipts", tenant_id="default", version=1, package=package))
        txn.execution_snapshot = None
        file = await session.get(FileRecord, "receipt-scan")
        file.document_meta = {}
        file.status = "completed"
        old = receipts()
        old["records"][0]["record_index"] = {"$value": "client index"}
        file.result = old
        await session.commit()
    assert (await client.get(URL)).json()["output_shape"] == "list"
    docs = (await client.get("/api/v1/transactions/receipts-txn/documents")).json()
    assert docs["files"][0]["documents"][0]["data"][0]["receipt"] == "R-001"
    status = (await client.get("/api/v1/status/receipts-txn?include_confidence_flag=false")).json()
    assert status["files"][0]["result"][0]["receipt"] == "R-001"
    table = await client.get("/api/v1/cabinet/receipts?limit=1")
    only, = table.json()["rows"]
    assert only["record_index"] == 1 and only["field.record_index"] == "client index"
    async with db.session_factory()() as session:
        file = await session.get(FileRecord, "receipt-scan")
        file.status = "pending_verification"
        await session.commit()
    assert (await client.post(URL + "/lock")).status_code == 200
    assert (await client.patch(URL + "/fields", json=patch_body(0, "receipt", "old job", 0))).status_code == 200
    assert (await client.post(URL + "/records", json={"expected_revision": 1})).status_code == 200
    assert (await client.delete(URL + "/records/0?expected_revision=2")).status_code == 200
