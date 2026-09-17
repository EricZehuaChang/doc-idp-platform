#!/usr/bin/env python3
"""9.15 WP5 基准报告 (R06): 均衡 vs 极速，同配置已发布技能对比。

输入（manifest JSON）:
{
  "skills": ["invoice_balanced", "invoice_fast"],   # 两个配置相同、仅处理模式不同
  "repeats": 3,
  "samples": [{"path": "samples/a.pdf",
               "expected": {"invoice_no": "INV-1"}}]
}

- Key 只从环境变量 IDP_BENCH_KEY 读取，绝不写入命令行或报告。
- 走 Playground /studio/runs（purpose=test）：不进任务列表/统计/数据柜/审单队列，
  不触发 webhook；每个技能先把样本上传为 studio 样本再运行。
- 输出 bench-report.json / bench-report.md：各阶段耗时 P50/P95 与字段准确率。
  报告只陈述实测数字与环境——不得写成“已达到 2–5 秒”之类的宣传口径。
- 仅限开发或测试环境运行；使用真实客户样本需先获得授权。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

TERMINAL = {"completed", "passed", "error", "rejected"}


def _client(base: str, key: str) -> httpx.Client:
    return httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"},
                        timeout=300.0)


def _upload_sample(c: httpx.Client, skill_code: str, path: Path) -> str:
    with open(path, "rb") as fh:
        r = c.post("/api/v1/studio/samples",
                   files={"file": (path.name, fh)},
                   data={"skill_code": skill_code})
    r.raise_for_status()
    return r.json()["id"]


def _run_once(c: httpx.Client, skill_code: str, sample_ids: list[str]) -> dict:
    r = c.post("/api/v1/studio/runs",
               json={"skill_code": skill_code, "sample_ids": sample_ids})
    r.raise_for_status()
    txn_id = r.json()["transaction_id"]
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        st = c.get(f"/api/v1/status/{txn_id}")
        st.raise_for_status()
        files = st.json()["files"]
        if files and all(f["status"] in TERMINAL for f in files):
            break
        time.sleep(1.0)
    else:
        raise TimeoutError(f"transaction {txn_id} did not finish in 600s")
    docs = c.get(f"/api/v1/transactions/{txn_id}/documents")
    docs.raise_for_status()
    return docs.json()


def _collect_metrics(report: dict) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for f in report["files"]:
        for doc in f["documents"]:
            m = doc.get("metrics") or {}
            for k in ("queue_ms", "parse_ms", "classify_ms", "extract_ms",
                      "total_ms"):
                if isinstance(m.get(k), (int, float)):
                    out.setdefault(k, []).append(float(m[k]))
    return out


def _accuracy(report: dict, expected: dict[str, str]) -> tuple[int, int]:
    hits = total = 0
    for f in report["files"]:
        for doc in f["documents"]:
            data = doc.get("data")
            if not isinstance(data, dict):
                continue
            for field, want in expected.items():
                if field not in data:
                    continue
                total += 1
                got = data.get(field)
                if isinstance(got, str) and got.strip() == str(want).strip():
                    hits += 1
    return hits, total


def _p50_p95(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    vs = sorted(values)
    p95 = vs[min(len(vs) - 1, max(0, round(0.95 * len(vs)) - 1))]
    return statistics.median(vs), p95


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--service", required=True, help="e.g. http://127.0.0.1:8201")
    ap.add_argument("--manifest", required=True, help="manifest JSON path")
    ap.add_argument("--out", default="bench-report", help="output file prefix")
    args = ap.parse_args()

    key = os.environ.get("IDP_BENCH_KEY")
    if not key:
        print("error: set IDP_BENCH_KEY (never pass keys on the command line)",
              file=sys.stderr)
        return 2

    manifest = json.loads(Path(args.manifest).read_text())
    skills = manifest["skills"]
    samples = manifest["samples"]
    repeats = int(manifest.get("repeats", 3))
    if len(skills) != 2:
        print("error: manifest.skills must list exactly two skills", file=sys.stderr)
        return 2

    results: dict[str, dict] = {}
    with _client(args.service, key) as c:
        me = c.get("/api/v1/agent/ping")
        if me.status_code != 200:
            print("error: key rejected by service", file=sys.stderr)
            return 2
        for skill in skills:
            sample_ids = [_upload_sample(c, skill, Path(s["path"]))
                          for s in samples]
            per_mode: dict[str, list] = {k: [] for k in
                                         ("queue_ms", "parse_ms", "classify_ms",
                                          "extract_ms", "total_ms")}
            acc_hits = acc_total = 0
            for _ in range(repeats):
                report = _run_once(c, skill, sample_ids)
                metrics = _collect_metrics(report)
                for k, vals in metrics.items():
                    per_mode[k].extend(vals)
                h, t = _accuracy(report, samples[0].get("expected", {}))
                acc_hits += h
                acc_total += t
            p50s: dict[str, float | None] = {}
            p95s: dict[str, float | None] = {}
            for k in per_mode:
                p50s[k], p95s[k] = _p50_p95(per_mode[k])
            results[skill] = {"p50_ms": p50s, "p95_ms": p95s,
                              "field_accuracy": (acc_hits / acc_total)
                              if acc_total else None,
                              "accuracy_samples": acc_total,
                              "repeats": repeats, "samples": len(samples)}

    payload = {"service": args.service, "skills": skills, "results": results,
               "note": "实测数字，仅陈述本机环境；不构成对外的速度承诺"}
    Path(f"{args.out}.json").write_text(json.dumps(payload, ensure_ascii=False,
                                                   indent=2))
    lines = ["# 处理模式基准报告", "",
             f"- service: `{args.service}`",
             f"- skills: {skills}（repeats={repeats}）", "",
             "| 技能 | 阶段 | P50 (ms) | P95 (ms) |", "|---|---|---|---|"]
    for skill, res in results.items():
        for stage in ("queue_ms", "parse_ms", "classify_ms", "extract_ms",
                      "total_ms"):
            lines.append(f"| {skill} | {stage} | {res['p50_ms'][stage]} | "
                         f"{res['p95_ms'][stage]} |")
        acc = res["field_accuracy"]
        lines.append(f"| {skill} | 字段准确率 | "
                     f"{f'{acc:.1%}' if acc is not None else '—'} |"
                     f" n={res['accuracy_samples']} |")
    lines += ["", "> 仅限开发/测试环境实测；报告只陈述实测数字与环境。"]
    Path(f"{args.out}.md").write_text("\n".join(lines) + "\n")
    print(f"written: {args.out}.json / {args.out}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
