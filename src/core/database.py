"""
Database Setup
==============
Centralized database engine, session factory, and startup logic.
"""

import os
import logging

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

from core.models import Base

logger = logging.getLogger("mock_platform")

# Config
DB_NAME = os.environ.get("DB_NAME", "mock_platform.db")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # points to src/
DB_PATH = os.path.join(BASE_DIR, "..", "data", DB_NAME)
DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# Engine + Session Factory
engine = create_async_engine(DB_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Create tables and run auto-migrations on startup."""
    data_dir = os.path.join(BASE_DIR, "..", "data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Simple Auto-Migration: Add request_schema if missing
    async with engine.connect() as conn:
        try:
            await conn.execute(text("ALTER TABLE endpoint_behavior ADD COLUMN request_schema JSON"))
            await conn.commit()
            logger.info("✅ Database Migrated: Added 'request_schema' column.")
        except Exception:
            pass  # Column likely already exists
