"""Runtime configuration. Responsibility: load env-level settings plus the two
config-as-code files (providers.yaml / parsers.yaml). Design v0.2 §11.10:
system-tier config lives in files/env and is NOT editable from any UI.
Secrets discipline (§6.1 BYOK): yaml holds api_key_env names only, never key values.
"""
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class ProviderCfg(BaseModel):
    name: str
    model: str
    base_url: str
    api_key_env: str = ""


class ParserCfg(BaseModel):
    name: str
    type: str                      # local | cloud_api | cloud_vlm
    base_url: str | None = None
    api_key_env: str | None = None
    for_: str | None = None

    model_config = {"populate_by_name": True}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IDP_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./data/idp.db"
    data_dir: Path = REPO_ROOT / "data"
    deploy_tier: str = "lite"      # lite | standard | air_gapped (design §8)
    default_tenant: str = "default"

    # task queue (§9): "inprocess" = lite tier M1 runner; "celery" ships work
    # to the resource-pooled queues (requires Redis + at least one worker)
    queue_backend: str = "inprocess"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # multi-doc split (M2 item 7): "auto" = LLM page classification on
    # multi-page files (one cheap call per file); "off" = never split
    multi_doc_split: str = "auto"

    # auth (§11.9): "off" = M1 dev/lite behavior (header tenant, no login);
    # "on" = /api requires Bearer JWT or API key, tenant comes from credential
    auth_mode: str = "off"
    # bootstrap admin, created at startup iff no user exists and password is set
    admin_email: str = "admin@example.com"  # @local would fail EmailStr validation on login
    admin_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_providers() -> dict:
    raw = yaml.safe_load((REPO_ROOT / "configs" / "providers.yaml").read_text(encoding="utf-8"))
    fb = raw.get("fallback") or []
    return {
        "active": raw["active"],
        # platform-level failover chain (str or list in yaml -> always a list)
        "fallback": [fb] if isinstance(fb, str) else list(fb),
        "providers": {p["name"]: ProviderCfg(**p) for p in raw["providers"]},
    }


@lru_cache
def load_parsers() -> dict:
    raw = yaml.safe_load((REPO_ROOT / "configs" / "parsers.yaml").read_text(encoding="utf-8"))
    parsers = {}
    for p in raw["parsers"]:
        p = dict(p)
        p["for_"] = p.pop("for", None)
        parsers[p["name"]] = ParserCfg(**p)
    return {"parsers": parsers, "default_parser": raw["default_parser"]}
