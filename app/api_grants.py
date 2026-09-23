"""Personal API credentials: key choices intersect the owner's live grant."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Group = Literal["process", "detect", "locate"]


class ApiGrants(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_create: bool = True
    groups: list[Group] = Field(default_factory=lambda: ["process"])
    allowed_skill_codes: list[str] | None = None


def grants_for(role: str, raw: dict | None) -> ApiGrants:
    if role == "admin":
        return ApiGrants(groups=["process", "detect", "locate"])
    grants = ApiGrants.model_validate(raw or {})
    if role == "viewer":
        grants.groups = [g for g in grants.groups if g == "process"]
    return grants


def intersect_skills(key: list[str] | None, owner: list[str] | None) -> list[str] | None:
    if owner is None:
        return key
    if key is None:
        return owner
    return [s for s in key if s in owner]


def scope_groups(scopes: str) -> set[str]:
    parts = set((scopes or "").split(","))
    groups = parts & {"detect", "locate"}
    if parts & {"skills:read", "process:write"}:
        groups.add("process")
    return groups
