"""Local dev launcher: inject model keys from the machine-wide secrets.yaml
(the `env` sibling directory), then start uvicorn. Safe to invoke from any
cwd — chdirs to the repo root first.
"""
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)
sys.path.insert(0, str(Path(__file__).resolve().parent))

# env dir differs per machine (WIN `D:/Claude Code/env`, MAC `~/code/env`);
# IDP_ENV_DIR overrides, otherwise take the first candidate that exists
_ENV_CANDIDATES = [os.environ.get("IDP_ENV_DIR"),
                   r"D:/Claude Code/env",
                   str(Path.home() / "code" / "env")]
for _cand in _ENV_CANDIDATES:
    if _cand and (Path(_cand) / "load_keys.py").is_file():
        sys.path.insert(0, _cand)
        break
from load_keys import load_keys  # noqa: E402

report = load_keys()
print("keys:", {k: v for k, v in report.items()})

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:create_app", factory=True, port=8200, host="127.0.0.1")
