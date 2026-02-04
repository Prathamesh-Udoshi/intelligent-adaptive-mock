import os
import time
import random
import asyncio
import datetime
from typing import List, Dict, Any, Optional

import httpx
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update

from models import Base, Endpoint, EndpointBehavior, ChaosConfig
from utils.normalization import normalize_path
from utils.schema_learner import learn_schema, generate_mock_response

# Config
TARGET_URL = os.environ.get("TARGET_URL", "http://httpbin.org")
DB_NAME = os.environ.get("DB_NAME", "mock_platform.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "data", DB_NAME)
DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"
LEARNING_BUFFER_SIZE = 10

app = FastAPI(title="Intelligent Adaptive Mock Platform")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DB Setup
engine = create_async_engine(DB_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Global Learning Buffer
LEARNING_BUFFER = []
buffer_lock = asyncio.Lock()

@app.on_event("startup")
async def startup():
    if not os.path.exists("./data"):
        os.makedirs("./data")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --- Dashboard & Legacy Admin Shims ---

@app.get("/")
async def get_dashboard():
    dashboard_path = os.path.join(BASE_DIR, "..", "static", "index.html")
    if os.path.exists(dashboard_path):
        return FileResponse(dashboard_path)
    return JSONResponse({"error": "Dashboard index.html not found"}, status_code=404)

@app.get("/admin/config")
async def get_config_shim():
    # Return basic config for the dashboard to initialize
    return {
        "chaos_level": 0,
        "learning_mode": True,
        "target_url": TARGET_URL
    }

@app.post("/admin/chaos")
async def set_chaos_shim(request: Request):
    # This shim updates all endpoints to simplify dashboard usage for now
    data = await request.json()
    level = data.get("level", 0)
    async with AsyncSessionLocal() as session:
        await session.execute(update(ChaosConfig).values(chaos_level=level, is_active=True))
        await session.commit()
    return {"status": "updated_globally", "level": level}

@app.post("/admin/learning")
async def set_learning_shim(request: Request):
    return {"status": "learning_mode_unsupported_globally_use_per_request"}

# --- Core Logic ---

async def get_or_create_endpoint(session: AsyncSession, method: str, path_pattern: str):
    result = await session.execute(
        select(Endpoint).where(Endpoint.method == method, Endpoint.path_pattern == path_pattern)
    )
    endpoint = result.scalars().first()
    
    if not endpoint:
        endpoint = Endpoint(method=method, path_pattern=path_pattern, target_url=TARGET_URL)
        session.add(endpoint)
        await session.flush()
        
        behavior = EndpointBehavior(endpoint_id=endpoint.id)
        chaos = ChaosConfig(endpoint_id=endpoint.id)
        session.add(behavior)
        session.add(chaos)
        await session.commit()
        
    return endpoint

async def process_learning_buffer():
    global LEARNING_BUFFER
    async with buffer_lock:
        if len(LEARNING_BUFFER) < LEARNING_BUFFER_SIZE:
            return
        batch = LEARNING_BUFFER[:]
        LEARNING_BUFFER = []

    async with AsyncSessionLocal() as session:
        for item in batch:
            method = item['method']
            path_pattern = item['path_pattern']
            status = item['status']
            latency = item['latency']
            body = item['body']
            
            endpoint = await get_or_create_endpoint(session, method, path_pattern)
            behavior_res = await session.execute(
                select(EndpointBehavior).where(EndpointBehavior.endpoint_id == endpoint.id)
            )
            behavior = behavior_res.scalars().first()
            
            # Update EMA for Latency
            alpha = 0.1
            behavior.latency_mean = (behavior.latency_mean * (1 - alpha)) + (latency * alpha)
            
            # Update status code distributions
            dist = behavior.status_code_distribution or {}
            status_str = str(status)
            for k in dist.keys():
                dist[k] *= (1 - alpha)
            dist[status_str] = dist.get(status_str, 0) + alpha
            
            # Normalize distribution
            total = sum(dist.values())
            behavior.status_code_distribution = {k: v/total for k, v in dist.items()}
            
            # Update Error Rate (e.g. 5xx status codes)
            is_error = 1.0 if status >= 500 else 0.0
            behavior.error_rate = (behavior.error_rate * (1 - alpha)) + (is_error * alpha)
            
            # Learn Schema
            if status < 300 and body:
                behavior.response_schema = learn_schema(behavior.response_schema, body)
            
            session.add(behavior)
        
        await session.commit()

# --- Admin API ---

@app.get("/admin/endpoints")
async def list_endpoints():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Endpoint))
        endpoints = res.scalars().all()
        return endpoints

@app.get("/admin/endpoints/{endpoint_id}/stats")
async def get_endpoint_stats(endpoint_id: int):
    async with AsyncSessionLocal() as session:
        b_res = await session.execute(select(EndpointBehavior).where(EndpointBehavior.endpoint_id == endpoint_id))
        behavior = b_res.scalars().first()
        c_res = await session.execute(select(ChaosConfig).where(ChaosConfig.endpoint_id == endpoint_id))
        chaos = c_res.scalars().first()
        
        if not behavior:
            raise HTTPException(status_code=404, detail="Endpoint not found")
            
        return {
            "behavior": {
                "latency_mean": behavior.latency_mean,
                "error_rate": behavior.error_rate,
                "status_codes": behavior.status_code_distribution,
                "schema_preview": behavior.response_schema
            },
            "chaos": {
                "level": chaos.chaos_level,
                "active": chaos.is_active
            }
        }

@app.post("/admin/endpoints/{endpoint_id}/chaos")
async def configure_chaos(endpoint_id: int, config: Dict[str, Any]):
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ChaosConfig)
            .where(ChaosConfig.endpoint_id == endpoint_id)
            .values(
                chaos_level=config.get("level", 0),
                is_active=config.get("active", False)
            )
        )
        await session.commit()
        return {"status": "updated"}

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def catch_all(request: Request, path: str, background_tasks: BackgroundTasks):
    method = request.method
    normalized = normalize_path(f"/{path}")
    mock_enabled = request.headers.get("X-Mock-Enabled", "false").lower() == "true"
    
    if method == "OPTIONS":
        return Response(status_code=200)

    async with AsyncSessionLocal() as session:
        endpoint = await get_or_create_endpoint(session, method, normalized)
        behavior_res = await session.execute(
            select(EndpointBehavior).where(EndpointBehavior.endpoint_id == endpoint.id)
        )
        behavior = behavior_res.scalars().first()
        
        chaos_res = await session.execute(
            select(ChaosConfig).where(ChaosConfig.endpoint_id == endpoint.id)
        )
        chaos = chaos_res.scalars().first()

    # 1. MOCK MODE
    if mock_enabled:
        # Apply Chaos
        effective_chaos = chaos.chaos_level if chaos.is_active else 0
        
        # Override via header
        header_chaos = request.headers.get("X-Chaos-Level")
        if header_chaos:
            effective_chaos = int(header_chaos)

        # Decide Error vs Success
        error_prob = behavior.error_rate + (effective_chaos / 100.0)
        if random.random() < min(error_prob, 0.9):
            # Simulate Failure
            return JSONResponse(
                content={"error": "Chaos Injected", "endpoint": normalized},
                status_code=500
            )

        # Simulate Latency
        latency = behavior.latency_mean + (effective_chaos * 10) # Simple chaos-latency link
        await asyncio.sleep(latency / 1000.0)
        
        # Choose Status Code from distribution
        codes = list(behavior.status_code_distribution.keys())
        probs = list(behavior.status_code_distribution.values())
        status_code = int(random.choices(codes, weights=probs)[0]) if codes else 200
        
        # Generate Body
        mock_body = generate_mock_response(behavior.response_schema)
        
        return JSONResponse(content=mock_body, status_code=status_code)

    # 2. PROXY MODE
    target_full_url = f"{TARGET_URL}/{path}"
    start_time = time.time()
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Prepare headers (exclude Host)
            headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
            
            proxy_resp = await client.request(
                method=method,
                url=target_full_url,
                headers=headers,
                params=dict(request.query_params),
                content=await request.body(),
                follow_redirects=False
            )
            
        latency_ms = (time.time() - start_time) * 1000
        
        # Buffer for background learning
        try:
            body_json = proxy_resp.json()
        except:
            body_json = None
            
        async with buffer_lock:
            LEARNING_BUFFER.append({
                "method": method,
                "path_pattern": normalized,
                "status": proxy_resp.status_code,
                "latency": latency_ms,
                "body": body_json
            })
            if len(LEARNING_BUFFER) >= LEARNING_BUFFER_SIZE:
                background_tasks.add_task(process_learning_buffer)
        
        return Response(
            content=proxy_resp.content,
            status_code=proxy_resp.status_code,
            headers=dict(proxy_resp.headers)
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Proxy Error: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
