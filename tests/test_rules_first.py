"""Rules use real Office/PDF parsers, with only the paid provider seam mocked."""
import json
import time
from zipfile import ZipFile

import pytest
from app.extraction.pipeline import extract
from app.extraction.rules import extract_rules
from app.parsers.base import UDR, Page, Block
from app.parsers.electronic import MarkitdownParser, PdfPlumberParser
from app.skillengine.schema import FieldSpec, SkillPackage, ExtractionRule, Validator, ModelBinding


def field(name='invoice', **kw):
    return FieldSpec(name=name, rule=ExtractionRule(labels=['Invoice']), **kw)


def pkg(fields, **kw):
    return SkillPackage(skill_code='rules_test', extraction_channel='rules_first', fields=fields, **kw)


def udr(text):
    return UDR(full_markdown=text, pages=[Page(page_no=1, blocks=[Block(text=text)])])


def no_model(*args, **kwargs):
    raise AssertionError('all fields were found: no paid model call is allowed')


def test_all_rules_no_model(monkeypatch):
    monkeypatch.setattr('app.extraction.pipeline.chat_json_with_fallback', no_model)
    p = pkg([field(), FieldSpec(name='total', type='number', rule=ExtractionRule(kind='regex', pattern=r'Total: ([0-9.]+)'))], model_binding=ModelBinding(challenger='challenger'))
    result, usage, review = extract(udr('Invoice: INV-1\nTotal: 12.50'), p)
    assert result['invoice']['$value'] == 'INV-1'
    assert result['total']['$source'] == 'rule'
    assert usage['rule_fields'] == 2 and usage['model_fields'] == 0
    assert not usage['model_called'] and usage['prompt_tokens'] == 0
    assert not review


def test_partial_contract_and_challenger(monkeypatch):
    calls = []
    def model(messages, chain, transport=None):
        contract = json.loads(messages[-1]['content'].split('## 文档内容')[0].split('\n', 1)[1])
        calls.append(contract)
        assert set(contract) == {'missing'}
        assert 'invoice' not in messages[0]['content']
        return {'invoice': 'wrong-extra-key', 'missing': 'value'}, {'prompt_tokens': 9}, chain[0] or 'test'
    monkeypatch.setattr('app.extraction.pipeline.chat_json_with_fallback', model)
    p = pkg([field(), FieldSpec(name='missing')], model_binding=ModelBinding(extractor='test', challenger='other'))
    result, usage, _ = extract(udr('Invoice: INV-1\nvalue'), p)
    assert len(calls) == 2
    assert result['invoice']['$value'] == 'INV-1' and '$challenger' not in result['invoice']
    assert result['missing']['$source'] == 'model'
    assert usage['rule_fields'] == 1 and usage['model_fields'] == 1


def test_invalid_rule_value_falls_back_and_review_miss(monkeypatch):
    def model(messages, chain, transport=None):
        return {'invoice': 'INV-2'}, {}, 'test'
    monkeypatch.setattr('app.extraction.pipeline.chat_json_with_fallback', model)
    result, usage, _ = extract(udr('Invoice: wrong'), pkg([field()], validators=[Validator(type='regex', field='invoice', pattern='INV-[0-9]+')]))
    assert result['invoice']['$value'] == 'INV-2' and usage['model_fields'] == 1
    monkeypatch.setattr('app.extraction.pipeline.chat_json_with_fallback', no_model)
    f = field()
    f.rule.on_miss = 'review'
    result, usage, review = extract(udr('Other text'), pkg([f], review_policy={'mode': 'never'}), fast=True)
    assert result['invoice']['$value'] == '' and result['invoice']['$source'] == 'review'
    assert review and usage['review_misses'] == ['invoice']


def test_vision_fallback_and_legacy_unchanged(monkeypatch):
    calls = []
    def model(messages, chain, transport=None):
        calls.append(messages)
        return {'invoice': 'INV-3'}, {'prompt_tokens': 2}, 'test'
    monkeypatch.setattr('app.extraction.pipeline.chat_json_with_fallback', model)
    f = field()
    f.rule.on_miss = 'review'
    result, usage, _ = extract(UDR(pages=[Page(page_no=1)]), pkg([f]), fast=True, page_images={1: 'data:image/png;base64,eA=='})
    assert usage['model_called'] and result['invoice']['$source'] == 'model'
    old = SkillPackage(skill_code='old', fields=[field()])
    one = extract(udr('Invoice: INV-1'), old)
    two = extract(udr('Invoice: INV-1'), old.model_copy(update={'extraction_channel': 'model'}))
    assert one == two and '$source' not in one[0]['invoice']


def test_table_sheet_headers_stop_and_incomplete():
    f = FieldSpec(name='items', type='table', columns=[FieldSpec(name='name'), FieldSpec(name='qty')],
                  rule=ExtractionRule(kind='table', sheet_name='Sales', column_aliases={'name': ['Product'], 'qty': ['Quantity']}))
    text = '## Other\n| Product | Quantity |\n| --- | --- |\n| hidden | 99 |\n## Sales\n| Product | Quantity |\n| --- | --- |\n| Bolt | 2 |\n| Total | 2 |\n| hidden | 100 |'
    assert extract_rules(udr(text), [f]) == {'items': [{'name': 'Bolt', 'qty': '2'}]}
    assert extract_rules(udr(text.replace('| Bolt | 2 |', '| Bolt | |')), [f]) == {}


def test_invalid_and_slow_regex_are_bounded():
    with pytest.raises(ValueError):
        ExtractionRule(kind='regex', pattern='(x)(y)')
    with pytest.raises(ValueError):
        ExtractionRule(kind='regex', pattern='[')
    f = FieldSpec(name='x', rule=ExtractionRule(kind='regex', pattern='(a+)+$'))
    start = time.monotonic()
    assert extract_rules(udr('a'*5000+'!'), [f]) == {}
    assert time.monotonic() - start < 3


@pytest.mark.parametrize('suffix', ['docx', 'xlsx', 'pdf'])
def test_real_parser_zero_model(tmp_path, monkeypatch, suffix):
    path = tmp_path / ('fixture.'+suffix)
    if suffix == 'docx':
        # Minimal OOXML fixture; parse through the real markitdown converter.
        with ZipFile(path, 'w') as z:
            z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Invoice: INV-42</w:t></w:r></w:p></w:body></w:document>')
        parsed = MarkitdownParser().parse(str(path))
    elif suffix == 'xlsx':
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = 'Sales'
        ws.append(['Invoice', 'INV-42'])
        wb.save(path)
        parsed = MarkitdownParser().parse(str(path))
    else:
        from reportlab.pdfgen.canvas import Canvas
        c = Canvas(str(path))
        c.drawString(72, 750, 'Invoice: INV-42')
        c.save()
        parsed = PdfPlumberParser().parse(str(path))
    monkeypatch.setattr('app.extraction.pipeline.chat_json_with_fallback', no_model)
    result, usage, _ = extract(parsed, pkg([field()]))
    assert result['invoice']['$value'] == 'INV-42', parsed.full_markdown
    assert not usage['model_called']
