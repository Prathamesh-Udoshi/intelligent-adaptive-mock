import asyncio
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from core.database import AsyncSessionLocal
from core.models import Endpoint
from sqlalchemy import select

async def check():
    async with AsyncSessionLocal() as s:
        res = await s.execute(select(Endpoint))
        endpoints = res.scalars().all()
        print(f"Found {len(endpoints)} endpoints")
        for e in endpoints:
            print(f"- {e.method} {e.path_pattern}")

if __name__ == "__main__":
    asyncio.run(check())
