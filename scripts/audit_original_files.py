#!/usr/bin/env python3
"""Read-only inventory of original files (WP6, 2026-09-09 plan §4).

For every FileRecord this script classifies where the row's blob *should* be
and whether it is actually there — the evidence base for deciding which of the
"下载失败（HTTP 500）" records can be restored, without touching anything:

- DB opened with ``file:...?mode=ro`` AND ``PRAGMA query_only=ON`` (double
  guard: even a future code path that tried to write would fail at the engine).
- Filesystem access is stat-only. No reads of customer bytes, no writes, no
  network, no backup-system calls.
- Legacy absolute paths (pre storage-key rows, e.g. Windows ``D:\\...``) are
  checked as-is AND against the shared-data relocation candidate: the suffix
  after the last ``/data/`` (or ``\\data\\``) segment appended to --data-dir.
  That mirrors migration 0002's prefix-strip logic and answers "would the file
  be findable under the new root?" without rewriting anything.

Usage:
    python scripts/audit_original_files.py --db /path/idp.db --data-dir /path/data
    python scripts/audit_original_files.py ... --json /tmp/audit.json

Exit codes: 0 = clean, 1 = missing originals found, 2 = usage error.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path, PureWindowsPath


def classify_path(storage_path: str) -> str:
    """key = relative storage key (post-WP2 rows); legacy_abs = POSIX absolute
    path; legacy_win = Windows absolute path (drive letter or backslashes)."""
    if PureWindowsPath(storage_path).is_absolute() and "\\" in storage_path:
        return "legacy_win"
    if storage_path.startswith("/"):
        return "legacy_abs"
    return "key"


def relocation_candidates(path: str, kind: str, data_dir: Path) -> list[Path]:
    """Places to look for the blob: the path itself, plus — for legacy rows —
    the data-dir-relative remainder after a `/data/` segment (upload rows were
    written as <old-root>/data/files/...; the tail is the stable part)."""
    out = []
    if kind == "key":
        out.append(data_dir / path)
    else:
        out.append(Path(path))
        norm = path.replace("\\", "/")
        low = norm.lower()
        idx = low.rfind("/data/")
        if idx >= 0:
            out.append(data_dir / norm[idx + len("/data/") :])
    # de-duplicate, keep order
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def blob_status(candidates: list[Path]) -> tuple[str, Path | None]:
    """ok (exists, >0 bytes) / empty / missing, plus the candidate that hit."""
    for c in candidates:
        try:
            if c.is_file() and c.stat().st_size > 0:
                return "ok", c
            if c.exists() and c.stat().st_size == 0:
                return "empty", c
        except OSError:
            continue
    return "missing", None


def audit(db_path: Path, data_dir: Path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    # self-check: any write attempt must fail right here
    try:
        con.execute("UPDATE files SET file_name = file_name")
        raise RuntimeError("query_only guard failed — refusing to continue")
    except sqlite3.OperationalError:
        pass
    rows = con.execute(
        "SELECT id, tenant_id, file_name, storage_path, udr_path, status, "
        "page_count, created_at FROM files ORDER BY created_at, id"
    ).fetchall()
    con.close()

    records, summary = (
        [],
        {
            "total": len(rows),
            "by_path_type": {},
            "blob_ok": 0,
            "blob_missing": 0,
            "blob_empty": 0,
            "udr_ok": 0,
            "udr_missing": 0,
            "relocatable": 0,  # missing as-is but found under --data-dir
        },
    )
    for fid, tenant, fname, spath, upath, status, pages, created in rows:
        kind = classify_path(spath)
        summary["by_path_type"][kind] = summary["by_path_type"].get(kind, 0) + 1
        cands = relocation_candidates(spath, kind, data_dir)
        st, hit = blob_status(cands)
        # "relocatable": the as-is path is gone but a data-dir candidate has it
        as_is_missing = not cands[0].is_file()
        relocatable = st == "ok" and as_is_missing and hit is not None and hit != cands[0]
        if upath:
            u_st, _ = blob_status(relocation_candidates(upath, classify_path(upath), data_dir))
        else:
            u_st = "missing"
        if st == "ok":
            summary["blob_ok"] += 1
        elif st == "empty":
            summary["blob_empty"] += 1
        else:
            summary["blob_missing"] += 1
        if u_st == "ok":
            summary["udr_ok"] += 1
        else:
            summary["udr_missing"] += 1
        if relocatable:
            summary["relocatable"] += 1
        records.append(
            {
                "file_id": fid,
                "tenant_id": tenant,
                "file_name": fname,
                "status": status,
                "page_count": pages,
                "created_at": created,
                "path_type": kind,
                "storage_path": spath,
                "blob": st,
                "blob_found_at": str(hit) if hit else None,
                "relocatable": relocatable,
                "udr": u_st,
            }
        )
    return {"summary": summary, "records": records}


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only original-file inventory (WP6)")
    ap.add_argument("--db", required=True, type=Path, help="SQLite DB path (opened read-only)")
    ap.add_argument(
        "--data-dir",
        required=True,
        type=Path,
        help="storage root for resolving relative keys / relocation candidates",
    )
    ap.add_argument(
        "--json",
        type=Path,
        default=None,
        help="optional path for the full JSON report (the DB is never written)",
    )
    args = ap.parse_args()
    if not args.db.is_file():
        print(f"db not found: {args.db}", file=sys.stderr)
        return 2
    if not args.data_dir.is_dir():
        print(f"data-dir not found: {args.data_dir}", file=sys.stderr)
        return 2

    report = audit(args.db, args.data_dir)
    s = report["summary"]
    print(f"files total: {s['total']}  path types: {s['by_path_type']}")
    print(f"original blob: ok={s['blob_ok']}  missing={s['blob_missing']}  empty={s['blob_empty']}")
    print(f"UDR json:      ok={s['udr_ok']}  missing={s['udr_missing']}")
    print(f"relocatable (missing as-is, found under data-dir): {s['relocatable']}")
    for r in report["records"]:
        if r["blob"] == "missing":
            print(
                f"  MISSING   {r['file_id']}  {r['file_name']:<40} "
                f"{r['path_type']:<10} status={r['status']}"
            )
    for r in report["records"]:
        if r["relocatable"]:
            print(
                f"  RELOCATABLE {r['file_id']}  {r['file_name']:<38} "
                f"found under new root: {r['blob_found_at']}"
            )
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json report: {args.json}")
    return 1 if s["blob_missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
