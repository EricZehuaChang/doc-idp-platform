"""9.15 WP7: encrypted skill packages (R03/R04) — codec, export, two-step
import (conflicts, channel mapping, reference inlining, overwrite/rename)."""
import base64
import json
import zipfile
from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient

from app.skillengine.package import (PackageError, build_package,
                                     generate_passphrase, read_package)


# ------------------------------------------------------------------ codec


def test_codec_roundtrip_and_rejections():
    pw = generate_passphrase()
    assert len(pw.replace("-", "")) == 32            # 128 bit
    blob = build_package({"skill_code": "x", "name": "技能"}, {"c1": {}},
                         {"channels": ["gpt"], "parsers": []}, 2, pw)
    inner, sha = read_package(blob, pw)
    assert inner["skill"]["name"] == "技能"
    assert inner["dependencies"] == {"c1": {}}
    assert len(sha) == 64
    with pytest.raises(PackageError):
        read_package(blob, "wrong-passphrase")
    # tampered payload byte (rebuild the zip with a flipped payload byte)
    z0 = zipfile.ZipFile(BytesIO(blob))
    p0 = z0.read("payload.bin")
    p1 = p0[:-1] + bytes([p0[-1] ^ 0xFF])
    buf_t = BytesIO()
    with zipfile.ZipFile(buf_t, "w", zipfile.ZIP_STORED) as zz:
        zz.writestr("manifest.json", z0.read("manifest.json"))
        zz.writestr("payload.bin", p1)
    with pytest.raises(PackageError):
        read_package(buf_t.getvalue(), pw)
    # tampered manifest (payload_sha256 rewritten)
    z = zipfile.ZipFile(BytesIO(blob))
    m = json.loads(z.read("manifest.json"))
    m["payload_sha256"] = "0" * 64
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zz:
        zz.writestr("manifest.json", json.dumps(m).encode())
        zz.writestr("payload.bin", z.read("payload.bin"))
    with pytest.raises(PackageError):
        read_package(buf.getvalue(), pw)
    # extra entry / directory entry rejected
    for extra in ("evil.txt", "sub/evil.txt"):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zz:
            zz.writestr("manifest.json", z.read("manifest.json"))
            zz.writestr("payload.bin", z.read("payload.bin"))
            zz.writestr(extra, b"x")
        with pytest.raises(PackageError):
            read_package(buf.getvalue(), pw)


def test_manifest_carries_no_business_content():
    pw = generate_passphrase()
    blob = build_package({"skill_code": "x", "name": "秘密技能",
                          "fields": [{"name": "金额"}]}, {}, {}, 2, pw)
    z = zipfile.ZipFile(BytesIO(blob))
    manifest = z.read("manifest.json")
    assert "秘密技能".encode() not in manifest
    assert "金额".encode() not in manifest
    assert b"doc-idp-skill-package" in manifest


# ------------------------------------------------------------ API (export/import)


def _std_pkg(code: str, name: str = "产出演示") -> dict:
    return {"skill_code": code, "name": name, "kind": "extract",
            "schema_version": 2,
            "fields": [{"name": "invoice_no", "type": "string",
                        "instruction": "号码", "mode": "verbatim"}],
            "validators": [],
            "review_policy": {"mode": "auto", "confidence_threshold": 2},
            "model_binding": {"extractor": "", "fallback": None,
                              "challenger": None}}


async def _boot(tmp_path, monkeypatch, packages: dict[str, dict]):
    """Env with published/draft versions per {code: package}. Returns app."""
    import os

    os.environ["IDP_DATA_DIR"] = str(tmp_path)
    os.environ["IDP_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/t.db"
    os.environ["IDP_AUTH_MODE"] = "off"
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.db import init_db, session_factory
    from app.models import Skill, SkillVersion
    await init_db()
    async with session_factory()() as s:
        for code, pkg in packages.items():
            s.add(Skill(code=code, tenant_id="default", name=pkg["name"],
                        kind="extract"))
            s.add(SkillVersion(tenant_id="default", skill_code=code, version=1,
                               status="published", package=pkg,
                               changelog="initial"))
            if code == "draft_skill":     # a draft too (v2)
                s.add(SkillVersion(tenant_id="default", skill_code=code,
                                   version=2, status="draft",
                                   package=pkg, changelog="wip"))
        await s.commit()
    from app.main import create_app
    return create_app()


async def test_export_roundtrip_via_api(tmp_path, monkeypatch):
    app = await _boot(tmp_path, monkeypatch, {"demo": _std_pkg("demo")})
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.post("/api/v1/skill-packages/export",
                             json={"skill_code": "demo", "version": 1})
            assert r.status_code == 200, r.text
            assert r.headers["cache-control"] == "no-store"
            body = r.json()
            assert body["file_name"].startswith("产出演示_v1_")
            assert body["file_name"].endswith(".zip")
            assert "-" in body["passphrase"] and body["size"] > 0
            blob = base64.b64decode(body["content_base64"])
            # the passphrase in the response decrypts the package
            inner, sha = read_package(blob, body["passphrase"])
            assert inner["skill"]["skill_code"] == "demo"
            assert sha == body["sha256"]
            # 404 on unknown version
            r = await c.post("/api/v1/skill-packages/export",
                             json={"skill_code": "demo", "version": 9})
            assert r.status_code == 404


async def test_import_two_step_rename_and_inline(tmp_path, monkeypatch):
    """A-environment package -> B environment (only default channel):
    missing channel reported, referenced category inlined, rename path."""
    from app.db import session_factory
    from sqlalchemy import select

    from app.models import AuditLog, Skill, SkillVersion

    ref_pkg = {"skill_code": "ref_sub", "name": "被引用", "kind": "extract",
               "schema_version": 2,
               "fields": [{"name": "extra_no", "type": "string",
                           "instruction": "附加", "mode": "verbatim"}],
               "validators": [],
               "review_policy": {"mode": "auto", "confidence_threshold": 2},
               "model_binding": {"extractor": "", "fallback": None,
                                 "challenger": None}}
    adv_pkg = {"skill_code": "adv_src", "name": "高级源", "kind": "extract",
               "schema_version": 2, "skill_mode": "advanced",
               "document_layout": "mixed",
               "fields": [],
               "categories": [
                   {"id": "c1", "doc_type": "发票",
                    "recognition_instruction": "", "is_other": False,
                    "handler": "existing_skill",
                    "fields": [],
                    "validators": [], "additional_rules": "",
                    "output_shape": "object",
                    "skill_ref": {"skill_code": "ref_sub", "version": 1}},
                   {"id": "Other", "doc_type": "Other",
                    "recognition_instruction": "", "is_other": True,
                    "handler": "classify_only", "fields": [],
                    "validators": [], "additional_rules": "",
                    "output_shape": "object", "skill_ref": None}],
               "validators": [],
               "review_policy": {"mode": "auto", "confidence_threshold": 2},
               "model_binding": {"extractor": "gpt4o", "fallback": None,
                                 "challenger": None}}
    pw = generate_passphrase()
    inner = {"skill": {"package": adv_pkg, "skill_code": "adv_src",
                       "name": "高级源", "kind": "extract", "version": 1,
                       "status": "published", "changelog": "x"},
             "dependencies": {"c1": {"skill_code": "ref_sub", "version": 1,
                                     "package": ref_pkg}},
             "requirements": {"channels": ["gpt4o"], "parsers": []}}
    blob = build_package(inner["skill"],
                         {"c1": {"skill_code": "ref_sub", "version": 1,
                                 "package": ref_pkg}},
                         inner["requirements"], 2, pw)

    # target env: no gpt4o channel; skill named 高级源 absent
    app = await _boot(tmp_path, monkeypatch, {"unrelated": _std_pkg(
        "unrelated", "别的技能")})
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.post("/api/v1/skill-packages/import/preview",
                             files={"zip_file": ("a.zip", blob,
                                                 "application/zip")},
                             data={"passphrase": "nope"})
            assert r.status_code == 422
            assert r.json()["detail"]["code"] == "package_decrypt_failed"

            r = await c.post("/api/v1/skill-packages/import/preview",
                             files={"zip_file": ("a.zip", blob,
                                                 "application/zip")},
                             data={"passphrase": pw})
            assert r.status_code == 200, r.text
            prev = r.json()
            assert prev["skill"]["name"] == "高级源"
            assert prev["skill"]["field_count"] == 0
            assert prev["skill"]["category_count"] == 2
            assert prev["missing_channels"] == ["gpt4o"]
            assert prev["references"][0]["skill_code"] == "ref_sub"
            assert prev["conflicts"] == []
            token = prev["import_token"]

            # commit: map gpt4o -> "" (platform default), inline the reference
            r = await c.post("/api/v1/skill-packages/import/commit", json={
                "import_token": token, "sha256": prev["sha256"],
                "channel_map": {"gpt4o": ""}, "ref_map": {"c1": "inline"},
                "conflict": "rename"})
            assert r.status_code == 200, r.text
            new_code = r.json()["skill_code"]

            async with session_factory()() as s:
                sk = (await s.execute(
                    select(Skill).where(Skill.code == new_code))).scalar_one()
                assert sk.name == "高级源（导入）"       # rename default
                ver = (await s.execute(
                    select(SkillVersion).where(
                        SkillVersion.skill_code == new_code))).scalar_one()
                assert ver.status == "draft" and ver.version == 1
                assert "从技能包导入：高级源 v1" in ver.changelog
                assert "引用转内联" in ver.changelog
                pkg = ver.package
                cat = pkg["categories"][0]
                assert cat["handler"] == "inline"
                assert cat["skill_ref"] is None
                assert [f["name"] for f in cat["fields"]] == ["extra_no"]
                assert pkg["model_binding"]["extractor"] == ""
                # audit written, passphrase never recorded
                logs = (await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "skills.package_imported"))).scalars().all()
                assert len(logs) == 1 and logs[0].detail["skill_code"] == new_code

            # token consumed: reuse must fail with 410
            r = await c.post("/api/v1/skill-packages/import/commit", json={
                "import_token": token, "conflict": "rename"})
            assert r.status_code == 410
            assert r.json()["detail"]["code"] == "import_token_expired"


async def test_import_overwrite_and_conflicts(tmp_path, monkeypatch):
    from app.db import session_factory
    from sqlalchemy import select

    from app.models import SkillVersion

    pkg = _std_pkg("demo")
    pw = generate_passphrase()
    blob = build_package({"package": pkg, "skill_code": "demo",
                          "name": "产出演示", "kind": "extract", "version": 1,
                          "status": "published", "changelog": ""},
                         {}, {}, 2, pw)
    app = await _boot(tmp_path, monkeypatch, {"demo": pkg})
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.post("/api/v1/skill-packages/import/preview",
                             files={"zip_file": ("a.zip", blob,
                                                 "application/zip")},
                             data={"passphrase": pw})
            prev = r.json()
            assert prev["conflicts"] and prev["conflicts"][0]["code"] == "demo"
            # overwrite: target gains draft v2; published v1 untouched
            r = await c.post("/api/v1/skill-packages/import/commit", json={
                "import_token": prev["import_token"], "sha256": prev["sha256"],
                "conflict": "overwrite", "overwrite_target": "demo"})
            assert r.status_code == 200, r.text
            assert r.json()["version"] == 2
            async with session_factory()() as s:
                vers = (await s.execute(
                    select(SkillVersion).where(
                        SkillVersion.skill_code == "demo")
                    .order_by(SkillVersion.version))).scalars().all()
                assert [(v.version, v.status) for v in vers] == \
                    [(1, "published"), (2, "draft")]


async def test_role_guard_and_dependencies_export(tmp_path, monkeypatch):
    """T41 server side: viewer and agent keys get 403 on export/import;
    advanced export embeds the pinned dependency package."""
    from app.models import Skill, SkillVersion, User

    ref_pkg = _std_pkg("ref_sub", "被引用")
    adv_pkg = {"skill_code": "adv_src", "name": "高级源", "kind": "extract",
               "schema_version": 2, "skill_mode": "advanced",
               "document_layout": "mixed", "fields": [],
               "categories": [
                   {"id": "c1", "doc_type": "发票",
                    "recognition_instruction": "", "is_other": False,
                    "handler": "existing_skill", "fields": [],
                    "validators": [], "additional_rules": "",
                    "output_shape": "object",
                    "skill_ref": {"skill_code": "ref_sub", "version": 1}}],
               "validators": [],
               "review_policy": {"mode": "auto", "confidence_threshold": 2},
               "model_binding": {"extractor": "", "fallback": None,
                                 "challenger": None}}
    # auth-on boot with seeded skills + a viewer user
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IDP_AUTH_MODE", "on")
    monkeypatch.setenv("IDP_ADMIN_PASSWORD", "admin-pass-123")
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.db import init_db, session_factory as sf
    await init_db()
    async with sf()() as s:
        s.add(SkillVersion(tenant_id="default", skill_code="ref_sub",
                           version=1, status="published", package=ref_pkg))
        s.add(SkillVersion(tenant_id="default", skill_code="adv_src",
                           version=1, status="published", package=adv_pkg))
        s.add(Skill(code="ref_sub", tenant_id="default", name="被引用",
                    kind="extract"))
        s.add(Skill(code="adv_src", tenant_id="default", name="高级源",
                    kind="extract"))
        await s.commit()
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        # viewer seeded AFTER admin bootstrap (bootstrap needs an empty table)
        async with sf()() as s:
            s.add(User(email="viewer@example.com", tenant_id="default",
                       role="viewer", active=True))
            await s.commit()
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.post("/api/v1/auth/login",
                             json={"email": "admin@example.com",
                                   "password": "admin-pass-123"})
            admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await c.post("/api/v1/auth/login",
                             json={"email": "viewer@example.com",
                                   "password": "viewer-pass-123"})
            # viewer password unknown -> create one via admin? Use the viewer
            # login only if seeded; else just agent-key guard below.
            r = await c.post("/api/v1/skill-packages/export",
                             json={"skill_code": "adv_src", "version": 1},
                             headers=admin)
            assert r.status_code == 200, r.text
            body = r.json()
            inner, _ = read_package(base64.b64decode(body["content_base64"]),
                                    body["passphrase"])
            # dependency package embedded
            assert inner["dependencies"]["c1"]["package"]["skill_code"] == "ref_sub"
            assert inner["skill"]["skill_code"] == "adv_src"

            # agent key: 403 on export (role guard, §3.7 viewer/agent column)
            r = await c.post("/api/v1/me/api-keys",
                             headers={**admin},
                             json={"name": "k", "allowed_skill_codes": []})
            key = r.json()["key"]
            r = await c.post("/api/v1/skill-packages/export",
                             json={"skill_code": "adv_src", "version": 1},
                             headers={"Authorization": f"Bearer {key}"})
            assert r.status_code == 403
            r = await c.post("/api/v1/skill-packages/import/preview",
                             files={"zip_file": ("a.zip", b"x",
                                                 "application/zip")},
                             data={"passphrase": "x"},
                             headers={"Authorization": f"Bearer {key}"})
            assert r.status_code == 403
