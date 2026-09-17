"""9.15 WP7 (R03/R04): encrypted skill-package codec (§3.8).

Pure module: stdlib + pydantic + cryptography only — no DB/framework imports
(architecture test enforces this). Format:
  zip (stored) ├─ manifest.json  plaintext metadata; NO business content
               └─ payload.bin    AES-256-GCM(inner JSON), scrypt KDF

AAD choice (documented deviation-free reading of §3.8): the manifest is the
AAD, but `payload_sha256` can only be known after encryption — so the AAD is
the canonical manifest bytes WITHOUT `payload_sha256`, and the written
manifest additionally carries `payload_sha256`. Import verifies that hash
first (any manifest tamper fails there) and then decrypts with the same AAD
(any payload tamper fails GCM). Either way the package is tamper-evident.
"""
from __future__ import annotations

import hashlib
import io
import json
import secrets
import zipfile
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = "doc-idp-skill-package"
FORMAT_VERSION = 1
EXPORTER_VERSION = "9.15-wp7"
MAX_ZIP_BYTES = 5 * 1024 * 1024
MAX_JSON_BYTES = 20 * 1024 * 1024
_ENTRIES = ("manifest.json", "payload.bin")


class PackageError(Exception):
    """Wrong passphrase or corrupted/illegal package (§3.8: one code,
    '口令错误或文件已损坏')."""


def generate_passphrase() -> str:
    """Server-generated >=128-bit secret, grouped for reading, never stored."""
    return "-".join(secrets.token_hex(2) for _ in range(8))   # 32 hex = 128 bit


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _kdf(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, n=32768, r=8, p=1,
                  length=32).derive(passphrase.encode("utf-8"))


def build_package(skill: dict, dependencies: dict, requirements: dict,
                  schema_version: int, passphrase: str) -> bytes:
    """Content + passphrase -> package bytes (§3.8)."""
    inner = _canonical({"skill": skill, "dependencies": dependencies,
                        "requirements": requirements})
    if len(inner) > MAX_JSON_BYTES:
        raise PackageError("inner JSON too large")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = _kdf(passphrase, salt)
    # manifest skeleton without payload_sha256 = the AAD (see module docstring)
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION,
                "schema_version": schema_version,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "exporter_version": EXPORTER_VERSION,
                "kdf": {"alg": "scrypt", "n": 32768, "r": 8, "p": 1,
                        "salt": salt.hex()},
                "cipher": {"alg": "AES-256-GCM", "nonce": nonce.hex()}}
    aad = _canonical(manifest)
    payload = AESGCM(key).encrypt(nonce, inner, aad)
    manifest["payload_sha256"] = hashlib.sha256(payload).hexdigest()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("manifest.json", _canonical(manifest))
        z.writestr("payload.bin", payload)
    return buf.getvalue()


def read_package(zip_bytes: bytes, passphrase: str) -> tuple[dict, str]:
    """Package bytes + passphrase -> (inner JSON dict, sha256 hex of the zip).
    Raises PackageError on wrong passphrase, tampering or illegal structure."""
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise PackageError("package too large")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise PackageError("not a zip") from e
    names = zf.namelist()
    if sorted(names) != sorted(_ENTRIES):
        raise PackageError("unexpected entries")
    for info in zf.infolist():
        if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise PackageError("directories/symlinks not allowed")
    manifest = json.loads(zf.read("manifest.json"))
    payload = zf.read("payload.bin")
    sha = hashlib.sha256(zip_bytes).hexdigest()
    if manifest.get("format") != FORMAT \
            or manifest.get("format_version") != FORMAT_VERSION:
        raise PackageError("unsupported format")
    if manifest.get("payload_sha256") != hashlib.sha256(payload).hexdigest():
        raise PackageError("payload digest mismatch")
    kdf, cipher = manifest.get("kdf", {}), manifest.get("cipher", {})
    if kdf.get("alg") != "scrypt" or cipher.get("alg") != "AES-256-GCM":
        raise PackageError("unsupported algorithms")
    aad = _canonical({k: v for k, v in manifest.items()
                      if k != "payload_sha256"})
    try:
        from cryptography.exceptions import InvalidTag
    except ImportError:                    # pragma: no cover
        InvalidTag = ()                    # type: ignore[assignment]
    try:
        key = _kdf(passphrase, bytes.fromhex(kdf["salt"]))
        inner = AESGCM(key).decrypt(bytes.fromhex(cipher["nonce"]),
                                    payload, aad)
    except (KeyError, ValueError, InvalidTag) as e:
        raise PackageError("decrypt failed") from e
    if len(inner) > MAX_JSON_BYTES:
        raise PackageError("inner JSON too large")
    try:
        return json.loads(inner), sha
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PackageError("inner JSON invalid") from e
