"""Architecture rules enforcement (docs/ARCHITECTURE.md §2).

AST-based import scan: the pure document core (parsers / skillengine /
extraction pipeline) must stay free of infrastructure imports so extraction
remains a pure function of (UDR, SkillPackage) — portable to workers, CLIs
and benchmark harnesses without dragging FastAPI/SQLAlchemy along.
Adapters that legitimately read tenant config from the DB (byok,
custom_providers) are exempt — runner warms them before the pure core runs.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN = {
    "app.db", "app.models", "app.tasks", "app.api",
    "app.billing", "app.notify", "app.integrations", "app.auth",
}

PURE_TREES = ("app/parsers", "app/skillengine")
PURE_FILES = (
    "app/extraction/pipeline.py",
    "app/extraction/confidence.py",
    "app/extraction/validators.py",
    "app/extraction/splitter.py",
    "app/extraction/provider_client.py",
)


def _top_level_module(node: ast.ImportFrom) -> str:
    """Resolve `from app.db import x` and `from . import y` shapes; relative
    imports (level > 0) never leave the package, so they are always safe."""
    if node.level > 0 or not node.module:
        return ""
    return node.module


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            m = _top_level_module(node)
            if m:
                mods.add(m)
    return mods


def _pure_files() -> list[Path]:
    files = [REPO_ROOT / f for f in PURE_FILES]
    for tree in PURE_TREES:
        files.extend(sorted((REPO_ROOT / tree).rglob("*.py")))
    return [f for f in files if "__pycache__" not in f.parts]


def test_pure_core_has_no_infrastructure_imports():
    violations = []
    for f in _pure_files():
        rel = f.relative_to(REPO_ROOT)
        for mod in sorted(_imported_modules(f)):
            if mod in FORBIDDEN:
                violations.append(f"{rel}: imports {mod}")
    assert not violations, (
        "pure document core must not import infrastructure "
        "(docs/ARCHITECTURE.md §2):\n" + "\n".join(violations))


# WP2: only the storage seam (and the bootstrap that owns data_dir itself)
# may touch Settings.data_dir — business code addresses blobs by key.
DATA_DIR_ALLOWED = {"app/config.py", "app/main.py"} | {
    str(p.relative_to(REPO_ROOT))
    for p in (REPO_ROOT / "app" / "storage").rglob("*.py")
    if "__pycache__" not in p.parts
}


def test_data_dir_only_touched_by_storage_layer():
    violations = []
    for f in sorted((REPO_ROOT / "app").rglob("*.py")):
        rel = str(f.relative_to(REPO_ROOT))
        if "__pycache__" in f.parts or rel in DATA_DIR_ALLOWED:
            continue
        if "data_dir" in f.read_text(encoding="utf-8"):
            violations.append(rel)
    assert not violations, (
        "Settings.data_dir must stay inside app/storage (docs/ARCHITECTURE.md "
        "§3):\n" + "\n".join(violations))
