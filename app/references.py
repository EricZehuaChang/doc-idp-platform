"""9.15 WP4 (R10): skill-reference resolution — server-side, tenant-checked.

A category with handler="existing_skill" points at another published skill via
`skill_ref = {skill_code, version}` (version null = follow the latest
published). The server is the only authority for what a reference means:
the editor never ships referenced package content to submit/dry-run (§WP4).

Snapshots: at submit time the resolved packages are frozen into
`transactions.execution_snapshot.dependencies`, so a later publish of the
referenced skill cannot change in-flight or historical behavior; published
referencing skills keep executing from their own snapshot even if the
referenced skill is later suspended or its versions deleted.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SkillVersion


class ReferenceUnavailable(LookupError):
    """A referenced skill/version does not resolve in this tenant."""

    def __init__(self, skill_code: str, version: int | None):
        self.skill_code = skill_code
        self.version = version
        super().__init__(f"reference unavailable: {skill_code} v{version}")


def _refs(pkg_dump: dict) -> list[dict]:
    out = []
    for c in pkg_dump.get("categories") or []:
        ref = c.get("skill_ref")
        if ref and ref.get("skill_code"):
            out.append(ref)
    return out


async def resolve_dependencies(s: AsyncSession, tenant_id: str,
                               pkg_dump: dict, self_code: str,
                               pin: bool = False) -> dict[str, dict]:
    """Resolve every skill_ref of a package to a concrete version row.

    pin=False (submit/dry-run): version null resolves to the latest published.
    pin=True (publish): version null is pinned to the current published one and
    the returned mapping notes which refs were newly pinned. Raises
    ReferenceUnavailable when a pinned version (or any version) is missing.
    Returns {skill_code: {"version": int, "package": dict}}.
    """
    deps: dict[str, dict] = {}
    for ref in _refs(pkg_dump):
        code = ref["skill_code"]
        if code == self_code:
            raise ReferenceUnavailable(code, ref.get("version"))
        want = ref.get("version")
        q = (select(SkillVersion)
             .where(SkillVersion.tenant_id == tenant_id,
                    SkillVersion.skill_code == code,
                    SkillVersion.status == "published"))
        rows = (await s.execute(q.order_by(SkillVersion.version.desc()))).scalars().all()
        row = next((r for r in rows if r.version == want), None) if want else \
            (rows[0] if rows else None)
        if row is None:
            raise ReferenceUnavailable(code, want)
        deps[code] = {"version": row.version, "package": dict(row.package or {})}
    return deps


def pin_references(pkg_dump: dict, deps: dict[str, dict]) -> dict:
    """Write resolved versions back into the package's skill_refs (publish-time
    pinning). Returns a copy; the draft keeps `version: null` (跟随最新发布版)."""
    out = {**pkg_dump, "categories": [
        {**c, "skill_ref": ({**c["skill_ref"],
                             "version": deps[c["skill_ref"]["skill_code"]]["version"]})
         if c.get("skill_ref") and c["skill_ref"].get("skill_code") in deps
         else c.get("skill_ref")}
        for c in pkg_dump.get("categories") or []]}
    return out


async def build_execution_snapshot(s: AsyncSession, tenant_id: str,
                                   skill_code: str, version: int) -> dict:
    """Immutable execution config for a submitted transaction: the exact
    package plus resolved dependency packages (§3.4). Legacy v1 packages
    (no categories) produce an empty dependencies map."""
    row = (await s.execute(
        select(SkillVersion).where(SkillVersion.skill_code == skill_code,
                                   SkillVersion.tenant_id == tenant_id,
                                   SkillVersion.version == version))).scalar_one_or_none()
    if row is None:
        raise LookupError(f"skill version not found: {skill_code} v{version}")
    pkg_dump = dict(row.package or {})
    deps = await resolve_dependencies(s, tenant_id, pkg_dump, skill_code)
    return {"package": pkg_dump, "dependencies": deps,
            "skill_version": version}


async def referencing_versions(s: AsyncSession, tenant_id: str,
                               skill_code: str, version: int | None
                               ) -> list[tuple[str, int, int | None]]:
    """Drafts or published versions of OTHER skills that reference
    (skill_code, version). version=None matches unpinned refs (跟随最新)."""
    q = (select(SkillVersion)
         .where(SkillVersion.tenant_id == tenant_id,
                SkillVersion.status.in_(("draft", "published")),
                SkillVersion.skill_code != skill_code))
    out: list[tuple[str, int, int | None]] = []
    for row in (await s.execute(q)).scalars():
        for ref in _refs(row.package or {}):
            if ref.get("skill_code") != skill_code:
                continue
            if version is None or ref.get("version") in (None, version):
                out.append((row.skill_code, row.version, ref.get("version")))
    return out
