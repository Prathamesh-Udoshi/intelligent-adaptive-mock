"""
Alembic Environment
====================
Configured for async SQLAlchemy (asyncpg on Postgres, aiosqlite locally).

The database URL is resolved from the same environment logic as database.py:
  - DATABASE_URL env var  →  PostgreSQL (Render / production)
  - fallback              →  SQLite    (local dev)

Run migrations:
  alembic revision --autogenerate -m "describe change"
  alembic upgrade head
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ── Make src/ modules importable ─────────────────────────────────────────────
# Alembic runs from the project root, so we add src/ to sys.path so that
# "from core.models import Base" resolves correctly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.models import Base  # noqa: E402  (must come after sys.path tweak)

# ── Alembic config ────────────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── Resolve the DB URL (mirrors database.py logic) ────────────────────────────
def _get_url() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if raw:
        if raw.startswith("postgres://"):
            raw = raw.replace("postgres://", "postgresql+asyncpg://", 1)
        elif raw.startswith("postgresql://") and "+asyncpg" not in raw:
            raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
        return raw

    # Local SQLite fallback
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_name = os.environ.get("DB_NAME", "mock_platform.db")
    db_path = os.path.join(base_dir, "data", db_name)
    return f"sqlite+aiosqlite:///{db_path}"


# ── Offline mode (generate SQL without connecting) ────────────────────────────
def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (connect and run) ─────────────────────────────────────────────
def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    cfg_section = config.get_section(config.config_ini_section, {})
    cfg_section["sqlalchemy.url"] = _get_url()

    connectable = async_engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


# ── Entry point ───────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
