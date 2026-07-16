"""Rule import channels (§5.1): natural-language -> LLM-drafted fields and
spreadsheet/CSV -> parsed fields. Both prefill the editor; nothing persists
until the user saves — these tests cover the draft endpoints only.
"""
import io

import pytest
from httpx import ASGITransport, AsyncClient

from app.skillengine import studio


# —— unit: table parsing ——

def test_fields_from_table_chinese_headers_and_nesting():
    rows = [
        ["字段名", "类型", "说明", "必填", "枚举值", "所属明细表"],
        ["invoice_no", "文本", "发票号码，去空格", "是", "", ""],
        ["total", "金额", "总金额，两位小数", "y", "", ""],
        ["currency", "枚举", "币种", "", "USD,EUR，JPY", ""],
        ["name", "文本", "品名", "", "", "items"],
        ["qty", "数字", "数量", "", "", "items"],
    ]
    fields = studio.fields_from_table(rows)
    by = {f.name: f for f in fields}
    assert by["invoice_no"].required is True and by["invoice_no"].type == "string"
    assert by["total"].type == "number" and by["total"].required is True
    assert by["currency"].enum_values == ["USD", "EUR", "JPY"]   # 中文逗号也拆
    assert by["items"].type == "table"
    assert [c.name for c in by["items"].columns] == ["name", "qty"]
    assert by["items"].columns[1].type == "number"


def test_fields_from_table_english_headers():
    rows = [["name", "type", "description", "required"],
            ["po_number", "string", "PO number", "true"]]
    fields = studio.fields_from_table(rows)
    assert fields[0].name == "po_number" and fields[0].required is True


def test_fields_from_table_missing_name_column():
    with pytest.raises(ValueError):
        studio.fields_from_table([["类型", "说明"], ["文本", "x"]])


# —— API level ——

@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            yield c


async def test_draft_from_text_prefills_fields(client, monkeypatch):
    def fake_chat(messages, chain, transport=None):
        assert "自然语言" in messages[0]["content"]
        assert "红字发票" in messages[1]["content"]
        return ({"doc_type": "海外发票", "fields": [
            {"name": "invoice_no", "type": "string", "required": True,
             "instruction": "发票号，去掉空格和连字符"},
            {"name": "is_credit_note", "type": "enum", "mode": "inferred",
             "enum_values": ["是", "否"], "instruction": "判断是否红字发票"},
            {"name": "items", "type": "table", "instruction": "明细行",
             "columns": [{"name": "qty", "instruction": "数量"}]},
        ]}, {"prompt_tokens": 50, "completion_tokens": 80}, "fake")

    monkeypatch.setattr(studio, "chat_json_with_fallback", fake_chat)
    r = await client.post("/api/v1/skills/draft-from-text",
                          json={"text": "抽发票号（去空格连字符）、明细行，并判断是否红字发票"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc_type"] == "海外发票"
    by = {f["name"]: f for f in body["fields"]}
    assert by["invoice_no"]["required"] is True
    assert by["is_credit_note"]["mode"] == "inferred"
    assert by["is_credit_note"]["enum_values"] == ["是", "否"]
    assert by["items"]["type"] == "table"
    assert by["items"]["columns"][0]["name"] == "qty"

    r = await client.post("/api/v1/skills/draft-from-text", json={"text": "   "})
    assert r.status_code == 400


async def test_draft_from_table_csv_and_xlsx(client):
    csv_bytes = ("字段名,类型,说明,必填\n"
                 "invoice_no,文本,发票号码,是\n"
                 "total,金额,总金额,\n").encode("utf-8")
    r = await client.post("/api/v1/skills/draft-from-table",
                          files={"file": ("fields.csv", csv_bytes, "text/csv")})
    assert r.status_code == 200, r.text
    names = [f["name"] for f in r.json()["fields"]]
    assert names == ["invoice_no", "total"]

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["name", "type", "instruction", "required", "parent"])
    ws.append(["po_number", "string", "PO 号", "true", ""])
    ws.append(["qty", "number", "数量", "", "items"])
    buf = io.BytesIO()
    wb.save(buf)
    r = await client.post("/api/v1/skills/draft-from-table",
                          files={"file": ("fields.xlsx", buf.getvalue(),
                                          "application/vnd.openxmlformats")})
    assert r.status_code == 200, r.text
    by = {f["name"]: f for f in r.json()["fields"]}
    assert by["po_number"]["required"] is True
    assert by["items"]["type"] == "table"
    assert by["items"]["columns"][0]["name"] == "qty"

    r = await client.post("/api/v1/skills/draft-from-table",
                          files={"file": ("x.docx", b"zz", "application/octet-stream")})
    assert r.status_code == 400
