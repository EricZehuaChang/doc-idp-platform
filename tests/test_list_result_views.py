"""PR-A result projections at the API boundary. Fixtures represent stored model output."""
import csv
import io
import json

import pytest
from sqlalchemy import select

from app.models import FileArtifact, FileRecord, Transaction
from tests.test_wp5_fast_and_playground import _boot
from tests.test_wp6_output import _boot_skill, _std_pkg


def receipts():
    records = []
    for i, receipt in enumerate(("R-001", "R-002")):
        records.append({
            "receipt": {"$value": receipt, "$confidence": 3 - i * 2,
                        "$bbox": [2, 3, 7, 8], "$pages": 1},
            "items": [{"name": ("tea", "rice")[i], "$cells": {"name": {"$confidence": 3}}}],
            "$record_bbox": [{"page": 1, "bbox": [2, 3, 7, 8]}],
        })
    return {"records": records}


@pytest.fixture
async def mixed_documents(tmp_path, monkeypatch):
    # A category-level List cannot be inferred from its parent Object configuration.
    async for client, db in _boot(tmp_path, monkeypatch):
        async with db.session_factory()() as session:
            txn = Transaction(id="mixed", tenant_id="default", skill_code="receipts",
                              skill_version=1, status="completed", execution_snapshot={
                                  "package": {"output_shape": "object"}})
            session.add(txn)
            await session.flush()
            common = dict(transaction_id=txn.id, tenant_id="default", storage_path="unused",
                          file_name="scan.png", page_count=1, status="completed")
            session.add(FileRecord(id="source", **common))
            await session.flush()
            for index, (file_id, shape, result) in enumerate([
                ("receipts-list", "list", receipts()),
                ("flat-object", "object", {
                    "receipt": {"$value": "F-01", "$confidence": 3},
                    "items": [{"name": "coffee", "$cells": {"name": {"$confidence": 3}}}],
                }),
            ]):
                session.add(FileRecord(id=file_id, parent_file_id="source", **common,
                                       document_meta={"doc_index": index + 1, "doc_type": file_id,
                                                      "source_pages": [1], "effective_schema": {
                                                          "output_shape": shape}},
                                       result=result))
            await session.commit()
        yield client


async def test_public_projections_resolve_each_child_shape(mixed_documents):
    docs = await mixed_documents.get("/api/v1/transactions/mixed/documents")
    assert docs.status_code == 200, docs.text
    by_id = {doc["file_id"]: doc for doc in docs.json()["files"][0]["documents"]}
    rows = [{"receipt": "R-001", "items": [{"name": "tea"}]},
            {"receipt": "R-002", "items": [{"name": "rice"}]}]
    flat = {"receipt": "F-01", "items": [{"name": "coffee"}]}
    assert by_id["receipts-list"]["data"] == rows
    assert by_id["receipts-list"]["review_fields"] == ["records[1].receipt"]
    assert by_id["flat-object"]["data"] == flat

    plain = await mixed_documents.get("/api/v1/status/mixed?include_confidence_flag=false")
    assert plain.status_code == 200, plain.text
    children = {c["file_id"]: c for c in plain.json()["files"][0]["children"]}
    assert children["receipts-list"]["result"] == rows
    assert children["flat-object"]["result"] == flat
    annotated = await mixed_documents.get("/api/v1/status/mixed")
    assert annotated.status_code == 200, annotated.text
    children = {c["file_id"]: c for c in annotated.json()["files"][0]["children"]}
    assert children["receipts-list"]["result"] == receipts()


async def test_cabinet_csv_projects_records_without_merging_their_items(mixed_documents):
    response = await mixed_documents.get("/api/v1/cabinet/receipts/export.csv")
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.text)))
    records = [row for row in rows if row["file_id"] == "receipts-list"]
    assert [r["receipt"] for r in records] == ["R-001", "R-002"]
    assert len({r["record_index"] for r in records}) == 2
    assert [json.loads(row["items"]) for row in records] == [[{"name": "tea"}], [{"name": "rice"}]]
    flat, = [row for row in rows if row["file_id"] == "flat-object"]
    assert flat["receipt"] == "F-01"
    # Legacy Object cabinet rows kept their raw table JSON (including cell metadata).
    assert json.loads(flat["items"]) == [{"name": "coffee", "$cells": {"name": {"$confidence": 3}}}]


async def test_list_artifact_naming_uses_first_records_scalar(tmp_path, monkeypatch):
    from app.db import session_factory
    from app.tasks.output_stage import output_stage

    package = _std_pkg("art", "rename", rule="{data.receipt}{original_ext}")
    package["output_shape"] = "list"
    async for _ in _boot_skill(tmp_path, monkeypatch, package):
        async with session_factory()() as session:
            file = (await session.scalars(select(FileRecord))).first()
            file.result = receipts()
            file.document_meta = {"output_shape": "list"}
            await session.commit()
            file_id = file.id
        await output_stage(file_id)
        async with session_factory()() as session:
            artifact = (await session.scalars(select(FileArtifact).where(
                FileArtifact.file_id == file_id))).one()
            assert (artifact.display_name, artifact.status) == ("R-001.pdf", "ready")
