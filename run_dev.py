"""Local dev launcher: inject model keys from the machine-wide secrets.yaml
(D:/Claude Code/env convention), then start uvicorn. Safe to invoke from any
cwd — chdirs to the repo root first.
"""
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, r"D:/Claude Code/env")
from load_keys import load_keys  # noqa: E402

report = load_keys()
print("keys:", {k: v for k, v in report.items()})

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:create_app", factory=True, port=8200, host="127.0.0.1")
