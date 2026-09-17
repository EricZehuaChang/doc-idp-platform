#!/usr/bin/env python
"""Cleanup editor studio samples older than N days (9.15 WP3, B6 default:
manual/cron, NOT automatic). Samples are sensitive working data: rows and
their blobs (original + cached UDR) are removed together.

Usage (repo root):
    IDP_DATA_DIR=./data IDP_DATABASE_URL=sqlite+aiosqlite:///./data/idp.db \
        .venv/bin/python scripts/cleanup_studio_samples.py [--days 30] [--dry-run]
"""
import argparse
import asyncio
import datetime as dt


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30,
                    help="delete samples older than this many days (default 30)")
    ap.add_argument("--dry-run", action="store_true", help="only print what would go")
    args = ap.parse_args()

    from sqlalchemy import delete, select
    from app.config import get_settings
    from app.db import get_engine, init_db, session_factory
    from app.models import StudioSample
    from app.storage import get_storage

    await init_db()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    sf = session_factory()
    st = get_storage()
    removed = kept = 0
    async with sf() as s:
        rows = (await s.execute(select(StudioSample))).scalars().all()
        for r in rows:
            created = r.created_at if r.created_at.tzinfo \
                else r.created_at.replace(tzinfo=dt.timezone.utc)
            if created >= cutoff:
                kept += 1
                continue
            removed += 1
            print(f"- {r.tenant_id}/{r.id} {r.file_name} ({created.date()})")
            if args.dry_run:
                continue
            for key in (r.storage_key,
                        f"studio/{r.tenant_id}/{r.id}/udr.json"):
                try:
                    st.delete(key)
                except OSError:
                    pass
        if not args.dry_run and removed:
            await s.execute(
                delete(StudioSample).where(StudioSample.created_at < cutoff))
            await s.commit()
    print(f"removed {removed}, kept {kept} (cutoff {cutoff.date()}, "
          f"backend={get_settings().storage_backend})")
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
