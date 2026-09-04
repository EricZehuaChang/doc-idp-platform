"""Built-in skill templates — the starter gallery (onboarding P0).

Why: a brand-new tenant cannot upload anything until a skill is published
(`UploadView` blocks on it), and the skill editor opens blank on concepts the
customer has never met. The gallery turns "design a skill" into "pick the one
that looks closest, fix two fields".

Templates are plain SkillPackage YAML under `templates/`, byte-identical in
shape to what `GET /skills/{code}/export` emits — so a template is just a
skill someone already built, and adding one means dropping a file in that
directory. No new serialisation format, no registry to keep in sync.

`Skill.code` is a GLOBAL primary key (models.py: tenant_id is an ordinary
column), so a template must never hand out its own id as the skill code —
the second tenant importing it would collide. `candidate_codes` derives a
per-tenant code instead; the caller picks the first one free.
"""
import hashlib
import logging
from collections.abc import Iterator
from pathlib import Path

from app.skillengine import studio
from app.skillengine.schema import SkillPackage

logger = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent / "templates"

# a template id must survive as the stem of a skill code (String(64)), leaving
# room for the tenant suffix and a de-duplication counter
_MAX_ID_LEN = 40


def _read(path: Path) -> SkillPackage:
    return studio.package_from_yaml(path.read_text(encoding="utf-8"))


def _paths() -> list[Path]:
    return sorted(p for p in _DIR.glob("*.yaml") if len(p.stem) <= _MAX_ID_LEN)


def summarise(template_id: str, pkg: SkillPackage) -> dict:
    """Gallery card payload: enough to choose without opening the editor."""
    tables = [f for f in pkg.fields if f.type == "table"]
    return {
        "id": template_id,
        "name": pkg.name or template_id,
        "description": pkg.description,
        "doc_type_hint": pkg.doc_type_hint,
        # scalar fields the reviewer will see, excluding the table containers
        "field_count": len(pkg.fields) - len(tables),
        "table_count": len(tables),
        # line-item columns are where most of the per-document value sits
        "column_count": sum(len(t.columns) for t in tables),
        "validator_count": len(pkg.validators),
    }


def list_templates() -> list[dict]:
    """All built-in templates as gallery cards. Unreadable files are skipped
    rather than failing the whole gallery — one bad template must not take the
    onboarding path down with it."""
    out = []
    for path in _paths():
        try:
            out.append(summarise(path.stem, _read(path)))
        except Exception:  # a broken template must not fail the whole gallery
            logger.warning("skill template %s is unreadable, skipped", path.name,
                           exc_info=True)
    return out


def get_template(template_id: str) -> SkillPackage | None:
    """Template by id, or None. The id is used as a filename, so anything with
    a path separator or a leading dot is rejected before touching the disk."""
    if not template_id or "/" in template_id or "\\" in template_id \
            or template_id.startswith(".") or len(template_id) > _MAX_ID_LEN:
        return None
    path = _DIR / f"{template_id}.yaml"
    if not path.is_file() or path.parent != _DIR:
        return None
    try:
        return _read(path)
    except Exception:  # a malformed template reads as absent, not as a 500
        logger.warning("skill template %s is unreadable", path.name, exc_info=True)
        return None


def candidate_codes(template_id: str, tenant: str) -> Iterator[str]:
    """Per-tenant skill codes to try, in order, for importing this template.

    The first is `<template>_<tenant hash>`; later ones append a counter so the
    same tenant can import a template more than once (e.g. one variant per
    customer). The tenant hash keeps two tenants off each other's code without
    leaking the tenant id into a value the other tenant could read back.
    """
    digest = hashlib.blake2s(tenant.encode("utf-8"), digest_size=3).hexdigest()
    base = f"{template_id}_{digest}"
    yield base
    for n in range(2, 100):
        yield f"{base}_{n}"
