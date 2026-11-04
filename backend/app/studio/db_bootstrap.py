"""Built during the Meta Global AI Developer Hackathon (new work).

Brings the database to the current schema before the server starts (the container runs `python -m app.studio.db_bootstrap`, then uvicorn).

Why this exists: the baseline migrations cannot be replayed on an empty database (the first one alters a table that only the app's own
`create_all` makes), and the old start command hid migration errors (`alembic upgrade head 2>/dev/null; ...`). So:

  * fresh database (no `users` table, no alembic_version): create the `vector` extension, create every table from the models, then
    `alembic stamp head`, so later deployments only run the new migrations;
  * database with an alembic_version: `alembic upgrade head` (errors stop the container, so a bad migration is never ignored);
  * tables but no alembic_version (a database made by `create_all` alone): stop with a clear message instead of guessing a revision.

In production the variable check (app/studio/prod_check.py) runs first, so a missing variable is reported by name instead of as a connection error.
Exit code 0 on success, 1 on any failure. Never prints the database URL.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1) if url.startswith("postgresql://") else url


async def inspect_state(url: str) -> str:
    """'fresh' | 'versioned' | 'unversioned' (and makes sure the vector extension exists)."""
    engine = create_async_engine(_async_url(url))
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            has_version = (await conn.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))).scalar()
            if has_version and (await conn.execute(text("SELECT count(*) FROM alembic_version"))).scalar():
                return "versioned"
            has_users = (await conn.execute(text("SELECT to_regclass('public.users') IS NOT NULL"))).scalar()
            return "unversioned" if has_users else "fresh"
    finally:
        await engine.dispose()


async def create_all_tables(url: str) -> None:
    import app.main  # noqa: F401  (imports every router and model, so every table is registered: otherwise the server processes would
    #                       create the missing ones themselves, all at once, and race each other)
    from app.core.database import Base
    engine = create_async_engine(_async_url(url))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def _alembic(*args: str) -> None:
    """Run the `alembic` command line tool. NOT `python -m alembic`: from the project directory that finds the project's own
    `alembic/` migrations folder instead of the package and fails (the old start command hid exactly this with 2>/dev/null)."""
    exe = os.path.join(os.path.dirname(sys.executable), "alembic")
    subprocess.run([exe if os.path.exists(exe) else (shutil.which("alembic") or "alembic"), *args], check=True)


def main() -> int:
    try:   # the baseline refuses placeholder SECRET_KEY / JWT_SECRET and DEBUG in production while the settings load: show it as one line
        from app.core.config import settings
    except RuntimeError as e:
        print(f"[config] {e}", flush=True)
        return 1
    from app.studio import prod_check
    from app.studio.config import get_env
    try:   # production: name every missing variable before touching the database
        prod_check.enforce(settings, get_env)
    except RuntimeError as e:
        print(f"[config] {e}", flush=True)
        return 1
    try:
        state = asyncio.run(inspect_state(settings.DATABASE_URL))
        print(f"[db] state: {state}", flush=True)
        if state == "fresh":
            asyncio.run(create_all_tables(settings.DATABASE_URL))
            _alembic("stamp", "head")
            print("[db] fresh database: tables created, stamped at head", flush=True)
        elif state == "versioned":
            _alembic("upgrade", "head")
            print("[db] alembic upgrade head done", flush=True)
        else:
            print("[db] ERROR: the database has tables but no alembic_version. Refusing to guess a revision. "
                  "Stamp it by hand (alembic stamp <revision>) or start from an empty database.", flush=True)
            return 1
    except Exception as e:  # noqa: BLE001 - one clear line, no URL
        print(f"[db] ERROR: {type(e).__name__}: {str(e).splitlines()[0][:300] if str(e) else ''}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
