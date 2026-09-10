// Recognition-result export (review page): turn one reviewed document's result
// into a downloadable JSON or Markdown file, entirely client-side — the review
// page already holds the whole result, so no new API surface is needed.
//
// Deliberate shape decisions:
//  - Values only. The `$`-prefixed engine metadata ($confidence/$bbox/$pages/
//    $cells) is stripped: the export is the *result*, not the engine's working
//    state. Downstream consumers read it straight.
//  - What is on screen is what is exported: the field values the reviewer is
//    currently looking at, including edits not yet saved.
//  - Markdown keeps the confidence column — that is the one piece of review
//    signal a human reader needs to spot a field worth a second look.

/** One document's on-screen result, assembled by the caller (ReviewView). */
export interface ResultExportInput {
  fileId: string;
  fileName: string;
  /** raw lifecycle status ("passed", "pending_verification", …) */
  status: string;
  /** the same status as displayed in the UI, so the export never disagrees */
  statusLabel: string;
  pageCount: number;
  verifiedBy: string | null;
  fields: { name: string; value: string; confidence: number }[];
  /** table rows keyed by column; `$`-prefixed keys are dropped on export */
  tables: { name: string; rows: Record<string, string>[] }[];
}

export type ResultFormat = "json" | "md";

/** Strip engine metadata and normalise a table row to column -> text. */
function cleanRow(row: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [col, value] of Object.entries(row))
    if (!col.startsWith("$")) out[col] = value ?? "";
  return out;
}

/** Column union in first-seen order — the same rule the review table uses. */
function columnsOf(rows: Record<string, string>[]): string[] {
  const cols = new Set<string>();
  for (const row of rows)
    for (const col of Object.keys(row)) if (!col.startsWith("$")) cols.add(col);
  return [...cols];
}

const pad = (n: number) => String(n).padStart(2, "0");
/** Local wall-clock stamp: a report is read by a person, not parsed. */
function stamp(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A markdown table cell: `|` would break the row, newlines would break the table. */
function mdCell(value: string): string {
  return (value ?? "").replace(/\|/g, "\\|").replace(/\r?\n/g, "<br>");
}

/** Clean result object: field -> value, table -> row array, no `$` keys. */
export function buildResultJson(input: ResultExportInput,
                                at: Date): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const f of input.fields) result[f.name] = f.value;
  for (const t of input.tables) result[t.name] = t.rows.map(cleanRow);
  return {
    file_name: input.fileName,
    file_id: input.fileId,
    status: input.status,
    page_count: input.pageCount,
    verified_by: input.verifiedBy,
    exported_at: at.toISOString(),
    result,
  };
}

/** Human-readable report: metadata, scalar fields, then one table per detail list. */
export function buildResultMarkdown(input: ResultExportInput, at: Date): string {
  const lines: string[] = [
    `# 识别结果 · ${input.fileName}`,
    "",
    `- 文件 ID：\`${input.fileId}\``,
    `- 状态：${input.statusLabel}`,
    `- 页数：${input.pageCount}`,
    `- 校验人：${input.verifiedBy || "—"}`,
    `- 导出时间：${stamp(at)}`,
    "",
    "## 字段",
    "",
  ];
  if (!input.fields.length) {
    lines.push("（无字段）", "");
  } else {
    lines.push("| 字段 | 值 | 置信度 |", "| --- | --- | --- |");
    for (const f of input.fields)
      lines.push(`| ${mdCell(f.name)} | ${mdCell(f.value)} | ${f.confidence} |`);
    lines.push("");
  }
  for (const t of input.tables) {
    lines.push(`## 明细表：${t.name}`, "");
    const cols = columnsOf(t.rows);
    if (!t.rows.length) lines.push("（无明细行）", "");
    else if (!cols.length) lines.push("（无可见列）", "");
    else {
      lines.push(`| ${cols.map(mdCell).join(" | ")} |`,
                 `| ${cols.map(() => "---").join(" | ")} |`);
      for (const row of t.rows)
        lines.push(`| ${cols.map((c) => mdCell(row[c] ?? "")).join(" | ")} |`);
      lines.push("");
    }
  }
  return lines.join("\n");
}

function downloadText(filename: string, text: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Build and download one document's result as `json` or `md`. */
export function downloadResult(input: ResultExportInput, format: ResultFormat): void {
  const at = new Date();
  const stem = input.fileName.replace(/\.[^.]+$/, "") || input.fileId;
  if (format === "json") {
    downloadText(`${stem}.json`, JSON.stringify(buildResultJson(input, at), null, 2),
                 "application/json");
  } else {
    downloadText(`${stem}.md`, buildResultMarkdown(input, at), "text/markdown");
  }
}
