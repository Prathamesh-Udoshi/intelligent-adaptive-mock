"""
Database Setup
==============
Supports two backends:

  • PostgreSQL (production / team) — set DATABASE_URL env var.
    Render injects this automatically when a Postgres service is linked.
    NOTE: Render uses the legacy "postgres://" scheme; we rewrite it to
    "postgresql+asyncpg://" automatically.

  • SQLite (local development) — used when DATABASE_URL is absent.
    No extra setup needed; the file lives in data/<DB_NAME>.
"""

import os
import logging

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from core.models import Base

logger = logging.getLogger("mock_platform")

# ── Resolve connection URL ─────────────────────────────────────────────────────

_raw_url = os.environ.get("DATABASE_URL", "")

if _raw_url:
    # Render (and some other providers) emit "postgres://" which SQLAlchemy no
    # longer recognises.  Rewrite it to the correct async dialect scheme.
    if _raw_url.startswith("postgres://"):
        _raw_url = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif _raw_url.startswith("postgresql://") and "+asyncpg" not in _raw_url:
        _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    DB_URL = _raw_url
    DB_BACKEND = "postgresql"
    logger.info("🐘 Database backend: PostgreSQL (DATABASE_URL detected)")
else:
    # Local development — SQLite
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DB_NAME = os.environ.get("DB_NAME", "mock_platform.db")
    _DB_PATH = os.path.join(_BASE_DIR, "..", "data", DB_NAME)
    DB_URL = f"sqlite+aiosqlite:///{_DB_PATH}"
    DB_BACKEND = "sqlite"
    logger.info(f"🗄️  Database backend: SQLite (local dev) → {_DB_PATH}")

# ── Engine & Session Factory ───────────────────────────────────────────────────

_engine_kwargs: dict = {"echo": False}

if DB_BACKEND == "postgresql":
    # Use a small connection pool; the free Render Postgres tier allows ~20 connections.
    _engine_kwargs.update({
        "pool_size": 5,
        "max_overflow": 10,
        "pool_pre_ping": True,         # Detect stale/dropped connections before use
        "pool_recycle": 300,           # Recycle connections after 5 min (prevents Render idle timeouts)
        "pool_timeout": 30,            # Wait up to 30s for a free connection before raising
    })

engine = create_async_engine(DB_URL, **_engine_kwargs)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── DB Initialisation ──────────────────────────────────────────────────────────

async def init_db():
    """
    Create all tables on startup (safe no-op if they already exist).

    For SQLite only: ensure the data/ directory exists.
    For PostgreSQL: tables are created via SQLAlchemy metadata; use Alembic
    for any subsequent schema migrations.
    """
    if DB_BACKEND == "sqlite":
        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..", "data"
        )
        os.makedirs(data_dir, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info(f"✅ Database tables verified / created ({DB_BACKEND}).")
