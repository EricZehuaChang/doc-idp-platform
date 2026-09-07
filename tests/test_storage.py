"""WP2 storage seam: keys are relative posix paths under the backend root;
legacy absolute paths stay readable; local_path materializes for local
tooling (parsers, LibreOffice, rasterizers)."""
from pathlib import Path

import pytest

from app.storage import LocalStorage, StorageError


@pytest.fixture
def st(tmp_path):
    return LocalStorage(tmp_path)


def test_key_roundtrip(st):
    assert st.put_bytes("udr/x.json", b"{}") == "udr/x.json"
    assert st.read_bytes("udr/x.json") == b"{}"
    assert st.exists("udr/x.json")
    assert st.size("udr/x.json") == 2


def test_rejects_bad_keys(st):
    for bad in ("../escape.json", "a/../../b", "/abs/x", ""):
        with pytest.raises(StorageError):
            st.put_bytes(bad, b"x")


def test_local_path_creates_parents(st):
    p = Path(st.local_path("deep/nested/file.bin"))
    assert p.parent.is_dir()
    p.write_bytes(b"1")
    assert st.read_bytes("deep/nested/file.bin") == b"1"


def test_legacy_absolute_path_passthrough(st, tmp_path):
    legacy = tmp_path / "legacy" / "f.pdf"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"P")
    assert st.exists(str(legacy))
    assert st.read_bytes(str(legacy)) == b"P"
    assert st.local_path(str(legacy)) == str(legacy)


def test_put_file_moves(st, tmp_path):
    src = tmp_path / "s.bin"
    src.write_bytes(b"m")
    st.put_file("x/s.bin", str(src))
    assert not src.exists()          # move semantics: tmp never lingers
    assert st.read_bytes("x/s.bin") == b"m"
