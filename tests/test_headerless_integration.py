"""PR-C: free-form header decisions survive extraction, storage and public review views."""
import hashlib
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.extraction import pipeline
from app.parsers.base import Block, Page, UDR
from app.skillengine.compiler import compile_messages
from app.skillengine.schema import SkillPackage
from tests.test_advanced_runner import Env


def package(shape="object", infer=False, columns=()):
    return SkillPackage(skill_code="open_table", schema_version=2, output_shape=shape,
                        fields=[{"name": "明细", "type": "table", "infer_headers": infer,
                                 "columns": [{"name": c} for c in columns]}],
                        review_policy={"mode": "never"})


def table(headers, rows):
    head = "".join(f"<th>{value}</th>" for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def source(*tables):
    md = "\n".join(tables)
    return UDR(parser="glm-ocr-cloud", full_markdown=md,
               pages=[Page(page_no=1, blocks=[Block(type="table", text=t) for t in tables])])


def mock_model(monkeypatch, raw):
    calls = []

    def chat(messages, chain, transport=None):
        calls.append(messages)
        return raw, {"prompt_tokens": 8, "completion_tokens": 4}, "fixture"

    monkeypatch.setattr(pipeline, "chat_json_with_fallback", chat)
    return calls


def test_numbered_first_row_recovers_and_unprinted_names_become_positional(monkeypatch):
    doc = source(table(["23", "树池", "6.50"], [["24", "花池", "8.00"]]))
    # Words printed elsewhere in the document cannot attest to a table heading.
    doc.full_markdown = "说明：复核序号、名称及单价。\n" + doc.full_markdown
    calls = mock_model(monkeypatch, {"明细": [{"序号": "24", "名称": "花池", "单价": "8.00"}]})
    result, usage, review = pipeline.extract(doc, package())
    assert len(calls) == 1
    prompt = "\n".join(m["content"] for m in calls[0])
    assert "列1" in prompt and "列2" in prompt
    assert result["明细"] == [{"列1": "23", "列2": "树池", "列3": "6.50"},
                            {"列1": "24", "列2": "花池", "列3": "8.00"}]
    assert usage["table_headers"]["明细"] == {
        "columns": ["列1", "列2", "列3"], "sources": dict.fromkeys(["列1", "列2", "列3"], "positional")}
    assert not review


def test_list_records_keep_distinct_header_origins(monkeypatch):
    doc = source(table(["项目", "数量"], [["绿植", "12"]]),
                 table(["23", "树池"], [["24", "花池"]]))
    raw = {"records": [
        {"明细": [{"项目": "绿植", "数量": "12"}]},
        {"明细": [{"序号": "23", "名称": "树池"}, {"序号": "24", "名称": "花池"}]},
    ]}
    mock_model(monkeypatch, raw)
    result, usage, review = pipeline.extract(doc, package(shape="list"))
    first, second = result["records"]
    assert first["明细"] == raw["records"][0]["明细"]
    assert second["明细"] == [{"列1": "23", "列2": "树池"}, {"列1": "24", "列2": "花池"}]
    assert usage["table_headers"]["records[0].明细"]["sources"] == {"项目": "original", "数量": "original"}
    assert usage["table_headers"]["records[1].明细"]["sources"] == {"列1": "positional", "列2": "positional"}
    assert not review


@pytest.mark.parametrize(("infer", "reason"), [(True, "table_headers_inferred"), (False, "table_shape_mismatch")])
async def test_header_decisions_persist_for_both_review_and_documents(tmp_path, monkeypatch, infer, reason):
    from app.main import create_app

    pkg = package(infer=infer)
    doc = source(table(["23", "树池", "6.50"], [["24", "花池", "8.00"]]))
    row = {"编号": "23", "名称": "树池"}
    if infer:
        row["价格"] = "6.50"
    mock_model(monkeypatch, {"明细": [row]})
    async with Env(tmp_path, monkeypatch, pkg) as env:
        txn_id, files, _, _ = await env.seed_and_run(
            monkeypatch, parse_fake=lambda *args, **kwargs: doc, extract_fake=pipeline.extract)
        file = files["bundle.pdf"]
        assert file.status == "pending_verification"
        assert reason in file.document_meta["review_reasons"]
        metadata = file.document_meta["table_headers"]
        assert set(metadata["明细"]["sources"].values()) == {"inferred" if infer else "positional"}
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
            response = await client.get(f"/api/v1/review/{file.id}")
            assert response.status_code == 200, response.text
            assert response.json()["table_headers"] == metadata
            assert reason in response.json()["review_reasons"]
            response = await client.get(f"/api/v1/transactions/{txn_id}/documents")
            assert response.status_code == 200, response.text
            public = response.json()["files"][0]["documents"][0]
            assert public["table_headers"] == metadata
            assert public["data"]["明细"] == file.result["明细"]


def test_defined_columns_keep_the_preexisting_object_prompt_and_rows(monkeypatch):
    pkg = package(columns=["项目", "数量"])
    doc = source(table(["项目", "数量"], [["纸", "7"]]))
    messages = compile_messages(pkg, doc)
    snapshot = hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest()
    assert snapshot == "4ea9c8b69986579dfe63cb32450c6a8a0869a9e3f94159580747fe23a5249bf7"
    mock_model(monkeypatch, {"明细": [{"项目": "纸", "数量": "7"}]})
    result, usage, _ = pipeline.extract(doc, pkg)
    assert not usage.get("table_headers")
    row, = result["明细"]
    assert row["项目"] == "纸" and row["数量"] == "7"
    assert set(row["$cells"]) == {"项目", "数量"}

def test_single_detected_source_can_belong_to_a_declared_table(monkeypatch):
    from app.skillengine.schema import FieldSpec

    pkg = package()
    pkg.fields.append(FieldSpec(name="库存", type="table", columns=[{"name": "名称"}, {"name": "数量"}]))
    doc = source(table(["名称", "数量"], [["针", "7"]]))
    mock_model(monkeypatch, {"库存": [{"名称": "针", "数量": "7"}],
                             "明细": [{"名称": "花池", "发货备注": "明天"}]})
    result, usage, _ = pipeline.extract(doc, pkg)
    assert result["库存"][0]["名称"] == "针"
    assert usage["table_headers"]["明细"]["sources"] == {"列1": "positional", "列2": "positional"}


async def test_record_deletion_reindexes_header_metadata_with_the_remaining_record(tmp_path, monkeypatch):
    from app.main import create_app

    pkg = package(shape="list")
    pkg.review_policy.mode = "always"
    doc = source(table(["项目", "数量"], [["绿植", "12"]]),
                 table(["23", "树池"], [["24", "花池"]]))
    records = [{"明细": [{"项目": "绿植", "数量": "12"}]},
               {"明细": [{"序号": "23", "名称": "树池"}, {"序号": "24", "名称": "花池"}]}]
    mock_model(monkeypatch, {"records": records})
    async with Env(tmp_path, monkeypatch, pkg) as env:
        txn_id, files, _, _ = await env.seed_and_run(
            monkeypatch, parse_fake=lambda *args, **kwargs: doc, extract_fake=pipeline.extract)
        file = files["bundle.pdf"]
        path = f"/api/v1/review/{file.id}"
        keep = file.document_meta["table_headers"]["records[1].明细"]
        remaining = file.result["records"][1]
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
            assert file.status == "pending_verification"
            locked = await client.post(path + "/lock")
            assert locked.status_code == 200, locked.text
            remove = path + "/records/0?expected_revision=0"
            response = await client.delete(remove)
            assert response.status_code == 200, response.text
            assert (await client.delete(remove)).status_code == 409
            after = (await client.get(path)).json()
            assert after["table_headers"] == {"records[0].明细": keep}
            assert after["result"] == {"records": [remaining]}
            public = (await client.get(f"/api/v1/transactions/{txn_id}/documents")).json()
            assert public["files"][0]["documents"][0]["table_headers"] == after["table_headers"]
