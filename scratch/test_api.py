
import asyncio
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

async def test_api():
    from core.database import AsyncSessionLocal
    from core.models import Endpoint
    from sqlalchemy import select
    import json
    
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Endpoint))
        endpoints = res.scalars().all()
        print(f"Endpoints count: {len(endpoints)}")
        
        data = []
        for ep in endpoints:
            d = {
                "id": ep.id,
                "method": ep.method,
                "path_pattern": ep.path_pattern,
                "target_url": ep.target_url,
                "created_at": str(ep.created_at)
            }
            data.append(d)
        print(f"Serialized data: {json.dumps(data)}")

if __name__ == "__main__":
    asyncio.run(test_api())
