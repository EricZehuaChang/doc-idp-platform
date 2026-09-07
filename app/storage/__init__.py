"""Storage seam (WP2, docs/ARCHITECTURE.md §3): every blob the platform keeps
(originals, UDR json, split PDFs, preview PDFs, golden samples) is addressed
by a *storage key* — a relative posix path under the backend root — instead
of an absolute filesystem path. DB rows store keys; `local_path()` resolves a
key to a real filesystem path for local-only tooling (parsers, LibreOffice,
rasterizers). Legacy rows written before WP2 hold absolute paths; LocalStorage
passes those through untouched (read compatibility) so pre-adoption databases
keep working — migration 0002 optionally rewrites them to keys.

Only LocalStorage exists today (lite tier, single host). The seam exists so a
multi-host Celery deployment can add an object-storage backend without
touching business code — do not add backends speculatively.
"""
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.config import get_settings


class StorageError(RuntimeError):
    """Raised for malformed keys or an unconfigured backend."""


def _validate_key(key: str) -> PurePosixPath:
    p = PurePosixPath(key)
    if not key or p.is_absolute() or ".." in p.parts:
        raise StorageError(f"invalid storage key: {key!r}")
    return p


class Storage(Protocol):
    """Blob-addressed storage. Keys are relative posix paths ('udr/x.json')."""

    def put_bytes(self, key: str, data: bytes) -> str: ...
    def put_file(self, key: str, src_path: str) -> str: ...
    def read_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...
    def local_path(self, key: str) -> str: ...


class LocalStorage:
    """Single-host filesystem backend rooted at Settings.data_dir.

    Writes are key-only (a legacy absolute path is never a write target);
    reads pass legacy absolute paths through untouched so pre-WP2 database
    rows keep working."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _key_path(self, key: str) -> Path:
        return self.root / _validate_key(key)

    def _read_path(self, key: str) -> Path:
        if os.path.isabs(key):           # legacy absolute path: read as-is
            return Path(key)
        return self._key_path(key)

    def put_bytes(self, key: str, data: bytes) -> str:
        path = self._key_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def put_file(self, key: str, src_path: str) -> str:
        path = self._key_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src_path, path)      # move: tmp may sit on another device
        return key

    def read_bytes(self, key: str) -> bytes:
        return self._read_path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._read_path(key).exists()

    def size(self, key: str) -> int:
        return self._read_path(key).stat().st_size

    def local_path(self, key: str) -> str:
        """Materialize the key as a filesystem path (Local: just the resolved
        path; parents created on demand so local tooling can write into it)."""
        path = self._read_path(key)
        if not os.path.isabs(key):
            path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)


_storage: Storage | None = None
_storage_sig: tuple[str, str] | None = None


def get_storage() -> Storage:
    """Backend selected from settings; the cached instance is invalidated when
    the backend or root changes (test suites re-point data_dir per test)."""
    global _storage, _storage_sig
    settings = get_settings()
    sig = (settings.storage_backend, str(settings.data_dir))
    if _storage is None or _storage_sig != sig:
        if settings.storage_backend != "local":
            raise StorageError(f"unknown storage backend: {settings.storage_backend}")
        _storage = LocalStorage(settings.data_dir)
        _storage_sig = sig
    return _storage
