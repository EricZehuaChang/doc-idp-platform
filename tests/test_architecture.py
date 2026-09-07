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
        f"(docs/ARCHITECTURE.md §2):\n" + "\n".join(violations))
