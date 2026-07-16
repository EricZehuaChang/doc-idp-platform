"""Dev-infrastructure smoke tests (docker-compose.dev.yml: PG16 + Redis 7).

These are integration probes, not unit tests: they SKIP (not fail) when the
containers are down, so the core suite stays green on machines without Docker.
Run `docker compose -f docker-compose.dev.yml up -d` first to exercise them.
"""
import socket

import pytest

PG_ADDR = ("127.0.0.1", 5432)
REDIS_ADDR = ("127.0.0.1", 6379)
PG_DSN = "postgresql://idp:idp_dev_pw@127.0.0.1:5432/idp"


def _reachable(addr: tuple[str, int]) -> bool:
    try:
        with socket.create_connection(addr, timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _reachable(PG_ADDR), reason="postgres container not running")
async def test_postgres_is_pg16():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(PG_DSN)
    try:
        version = await conn.fetchval("SHOW server_version")
    finally:
        await conn.close()
    assert version.startswith("16."), f"expected PG16, got {version}"


@pytest.mark.skipif(not _reachable(REDIS_ADDR), reason="redis container not running")
def test_redis_ping():
    # Raw RESP ping — no redis client dependency needed until Celery lands.
    with socket.create_connection(REDIS_ADDR, timeout=2) as s:
        s.sendall(b"PING\r\n")
        assert s.recv(16).startswith(b"+PONG")
