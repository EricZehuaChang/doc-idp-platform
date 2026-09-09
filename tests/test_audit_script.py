"""WP6: scripts/audit_original_files.py — read-only inventory of originals.

The script must classify every file row without ever writing: the DB is opened
mode=ro AND query_only=ON (verified here by asserting a write attempt fails and
the DB file is byte-identical after a run).
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_original_files.py"


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    (data_dir / "files").mkdir(parents=True)
    (data_dir / "files" / "a.pdf").write_bytes(b"%PDF-1.4 present")
    (data_dir / "files" / "legacy.pdf").write_bytes(b"%PDF-1.4 legacy")
    db = tmp_path / "idp.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE files (id TEXT PRIMARY KEY, tenant_id TEXT, file_name TEXT,"
        " storage_path TEXT, udr_path TEXT, status TEXT, page_count INTEGER,"
        " created_at TEXT)")
    con.executemany(
        "INSERT INTO files VALUES (?,?,?,?,?,?,?,?)",
        [
            ("ok1", "default", "a.pdf", "files/a.pdf", "udr/ok1.json",
             "passed", 1, "2026-09-01T00:00:00"),
            ("gone1", "default", "lost.pdf", "files/lost.pdf", None,
             "passed", 1, "2026-09-02T00:00:00"),
            ("old1", "default", "legacy.pdf",
             "D:\\idp\\data\\files\\legacy.pdf", None,
             "passed", 2, "2026-09-03T00:00:00"),
        ])
    con.commit()
    con.close()
    return db, data_dir


def _run(db: Path, data_dir: Path, json_out: Path | None):
    cmd = [sys.executable, str(SCRIPT), "--db", str(db),
           "--data-dir", str(data_dir)]
    if json_out:
        cmd += ["--json", str(json_out)]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_audit_classifies_missing_and_relocatable_without_writing(tmp_path):
    db, data_dir = _seed(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    out = tmp_path / "report.json"

    r = _run(db, data_dir, out)
    assert r.returncode == 1, r.stdout + r.stderr  # missing originals found

    s = json.loads(out.read_text())["summary"]
    assert s["total"] == 3 and s["blob_ok"] == 2 and s["blob_missing"] == 1
    assert s["by_path_type"] == {"key": 2, "legacy_win": 1}
    assert s["relocatable"] == 1
    recs = {x["file_id"]: x for x in json.loads(out.read_text())["records"]}
    assert recs["ok1"]["blob"] == "ok"
    assert recs["gone1"]["blob"] == "missing" and recs["gone1"]["udr"] == "missing"
    # the D:\\ path is gone, but the /data/ suffix found it under the new root
    assert recs["old1"]["blob"] == "ok" and recs["old1"]["relocatable"] is True
    assert recs["old1"]["blob_found_at"].endswith("files/legacy.pdf")
    assert "MISSING   gone1" in r.stdout and "RELOCATABLE old1" in r.stdout

    # zero writes: DB byte-identical, no -wal/-shm siblings, only the report
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not (tmp_path / "idp.db-wal").exists()


def test_ro_connection_refuses_writes(tmp_path):
    """The exact connection recipe the script uses must reject writes."""
    db, _ = _seed(tmp_path)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    try:
        con.execute("UPDATE files SET file_name = file_name")
        raise AssertionError("query_only guard failed - write went through")
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()


def test_audit_clean_db_exits_zero(tmp_path):
    db, data_dir = _seed(tmp_path)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM files WHERE id != 'ok1'")
    con.commit()
    con.close()
    r = _run(db, data_dir, None)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "missing=0" in r.stdout
