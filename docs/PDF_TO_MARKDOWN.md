# PDF 全文转 Markdown API

技能代码：`pdf_to_markdown`。先在所在租户创建并发布该技能，再为调用方的 API Key 授予此技能的提交与查询权限。接口沿用平台的 Bearer 鉴权；调用方只能读取其有权访问的任务。

技能使用 `kind: layout`、`include_markdown: true` 和空字段列表，固定走 `glm-ocr-cloud` 解析器。扫描 PDF 由 OCR 识别；不调用分类或字段提取模型，不需要配置业务字段。计费沿用平台的版面解析类型。发布技能的原始配置见 `app/skillengine/templates/pdf_to_markdown.yaml`。

## 提交

```bash
curl --fail-with-body \
  -H "Authorization: Bearer ${IDP_API_KEY}" \
  -F 'skill_code=pdf_to_markdown' \
  -F 'files=@scan.pdf;type=application/pdf' \
  https://docidp.ztenith.com.cn/api/v1/process
```

HTTP 202 表示已接收，响应包含 `transaction_id`。这是异步任务，需要继续查询；不要重复提交同一文档来轮询。接口也支持 `Idempotency-Key` 请求头。

## 查询并取全文

```bash
curl --fail-with-body \
  -H "Authorization: Bearer ${IDP_API_KEY}" \
  "https://docidp.ztenith.com.cn/api/v1/status/${TRANSACTION_ID}"
```

完成后的关键字段如下，其余原有状态、页数及版面坐标字段仍会返回：

```json
{
  "success": true,
  "transaction_id": "...",
  "status": "completed",
  "skill_code": "pdf_to_markdown",
  "files": [{
    "file_id": "...",
    "file_name": "scan.pdf",
    "status": "completed",
    "page_count": 2,
    "result": {},
    "markdown": "# 文档标题\n\n正文段落……\n\n| 项目 | 金额 |\n| --- | --- |\n| 示例 | 100 |"
  }]
}
```

读取每个文件的 `files[i].markdown`，用 UTF-8 写入 `.md` 文件即可。批次包含多个文件时逐个检查文件状态；错误信息在该文件的 `msg`，不要把错误任务视为已成功转换。

`GET /api/v1/transactions/{transaction_id}/documents` 也会在对应的 `files[i].documents[j].markdown` 返回相同内容。

## 返回约定

- `markdown` 仅对提交时明确开启全文输出的纯版面解析技能返回；普通字段提取技能和旧版面技能不增加此键。
- 文件为 `completed` 或 `passed` 且解析结果可读时返回字符串；排队、处理中、失败或解析结果不可用时为 `null`。合法的空白文档返回空字符串 `""`。
- 优先返回已保存的 OCR 全文 Markdown，保留原始换行、段落和表格；解析器有时使用 HTML 表格，这些内容也会原样返回。解析器仅提供逐页内容时按页序拼接。
- 内容来自本次 OCR，读取接口不重复识别，也不生成或猜测缺失文字。全文准确度需结合原件核验。
- 技能配置以提交时的任务快照为准；后来修改技能，不会使历史未开启任务新增全文输出。

如在网页中展示原始 Markdown，按客户端的 Markdown/HTML 安全策略处理其中的原文内容。
