import json
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from models import Base, Endpoint, EndpointBehavior, ChaosConfig
from utils.normalization import normalize_path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.environ.get("DB_NAME", "mock_platform.db")
DB_URL = f"sqlite+aiosqlite:///{os.path.join(BASE_DIR, '..', 'data', DB_NAME)}"
LOG_FILE = os.path.join(BASE_DIR, "..", "data", "production_logs.json")

engine = create_async_engine(DB_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def import_logs():
    if not os.path.exists(LOG_FILE):
        print(f"No logs found at {LOG_FILE}")
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with open(LOG_FILE, "r") as f:
        logs = json.load(f)

    print(f"Importing {len(logs)} logs into SQLite...")

    async with AsyncSessionLocal() as session:
        for entry in logs:
            method = entry.get("method", "GET")
            path = entry.get("endpoint", "/")
            normalized = normalize_path(path)
            status = entry.get("status", 200)
            latency = entry.get("latency_ms", 100.0)
            
            # Find or create endpoint
            res = await session.execute(
                select(Endpoint).where(Endpoint.method == method, Endpoint.path_pattern == normalized)
            )
            endpoint = res.scalars().first()
            if not endpoint:
                endpoint = Endpoint(method=method, path_pattern=normalized, target_url="http://imported")
                session.add(endpoint)
                await session.flush()
                
                behavior = EndpointBehavior(endpoint_id=endpoint.id)
                chaos = ChaosConfig(endpoint_id=endpoint.id)
                session.add(behavior)
                session.add(chaos)
            
            # Update behavior (simplified EMA update for import)
            res = await session.execute(
                select(EndpointBehavior).where(EndpointBehavior.endpoint_id == endpoint.id)
            )
            behavior = res.scalars().first()
            
            alpha = 0.05
            behavior.latency_mean = (behavior.latency_mean * (1 - alpha)) + (latency * alpha)
            is_error = 1.0 if status >= 500 else 0.0
            behavior.error_rate = (behavior.error_rate * (1 - alpha)) + (is_error * alpha)
            
            dist = behavior.status_code_distribution or {}
            s = str(status)
            dist[s] = dist.get(s, 0) + 1 # For batch import, we can do frequency then normalize at end
            behavior.status_code_distribution = dist
            
            session.add(behavior)

        # Final normalization of distributions
        res = await session.execute(select(EndpointBehavior))
        all_behaviors = res.scalars().all()
        for b in all_behaviors:
            dist = b.status_code_distribution
            if dist:
                total = sum(dist.values())
                b.status_code_distribution = {k: v/total for k, v in dist.items()}
                session.add(b)

        await session.commit()
    print("Import complete.")

if __name__ == "__main__":
    asyncio.run(import_logs())
