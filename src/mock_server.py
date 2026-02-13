import os
import re
import time
import random
import asyncio
import datetime
import logging
from typing import List, Dict, Any, Optional

import httpx
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update, text

from models import Base, Endpoint, EndpointBehavior, ChaosConfig
from utils.normalization import normalize_path
from utils.schema_learner import learn_schema, generate_mock_response

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("mock_platform")

# Config
TARGET_URL = os.environ.get("TARGET_URL", "http://httpbin.org")
DB_NAME = os.environ.get("DB_NAME", "mock_platform.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "data", DB_NAME)
DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"
LEARNING_BUFFER_SIZE = 1

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

# Global State
PLATFORM_STATE = {
    "mode": "proxy", # "proxy" or "mock"
    "learning_enabled": True
}

# Global Learning Buffer
LEARNING_BUFFER = []
RECENT_LOGS = [] # Store last 50 requests for dashboard monitoring
buffer_lock = asyncio.Lock()
logs_lock = asyncio.Lock()

# WebSocket Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                # Handle stale connections
                continue

manager = ConnectionManager()

@app.on_event("startup")
async def startup():
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
            # Column likely already exists
            pass

# --- Dashboard & Admin APIs ---

@app.get("/")
async def get_landing():
    landing_path = os.path.join(BASE_DIR, "..", "static", "landing.html")
    if os.path.exists(landing_path):
        return FileResponse(landing_path)
    return JSONResponse({"error": "Landing landing.html not found"}, status_code=404)

@app.get("/admin/dashboard")
async def get_dashboard():
    dashboard_path = os.path.join(BASE_DIR, "..", "static", "index.html")
    if os.path.exists(dashboard_path):
        return FileResponse(dashboard_path)
    return JSONResponse({"error": "Dashboard index.html not found"}, status_code=404)

@app.get("/admin/explorer")
async def get_explorer():
    explorer_path = os.path.join(BASE_DIR, "..", "static", "explorer.html")
    if os.path.exists(explorer_path):
        return FileResponse(explorer_path)
    return JSONResponse({"error": "Explorer explorer.html not found"}, status_code=404)

@app.get("/admin/docs")
async def get_swagger_ui():
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(
        openapi_url="/admin/export-openapi",
        title="AI Truth Swagger",
        swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png"
    )

@app.get("/admin/config")
async def get_config():
    return {
        "chaos_level": 0, # Note: This is per-endpoint but we return a generic 0 for init
        "learning_mode": PLATFORM_STATE["learning_enabled"],
        "platform_mode": PLATFORM_STATE["mode"],
        "target_url": TARGET_URL
    }

@app.post("/admin/chaos")
async def set_chaos_globally(request: Request):
    data = await request.json()
    level = data.get("level", 0)
    async with AsyncSessionLocal() as session:
        await session.execute(update(ChaosConfig).values(chaos_level=level, is_active=True))
        await session.commit()
    return {"status": "updated_globally", "level": level}

@app.post("/admin/learning")
async def toggle_learning(request: Request):
    data = await request.json()
    PLATFORM_STATE["learning_enabled"] = data.get("enabled", True)
    return {"status": "success", "learning_enabled": PLATFORM_STATE["learning_enabled"]}

@app.post("/admin/mode")
async def set_platform_mode(request: Request):
    data = await request.json()
    PLATFORM_STATE["mode"] = data.get("mode", "proxy")
    return {"status": "success", "mode": PLATFORM_STATE["mode"]}

@app.get("/admin/logs")
async def get_recent_logs():
    async with logs_lock:
        return RECENT_LOGS

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial logs on connection
        async with logs_lock:
            await websocket.send_json({"type": "initial", "data": RECENT_LOGS})
        
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# --- Core Logic ---

async def add_to_logs(method: str, path: str, status: int, latency: int, type: str):
    async with logs_lock:
        log_entry = {
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "method": method,
            "path": path,
            "status": status,
            "latency": round(latency),
            "type": type
        }
        RECENT_LOGS.insert(0, log_entry)
        if len(RECENT_LOGS) > 50:
            RECENT_LOGS.pop()
    
    # Broadcast to all dashboard clients
    await manager.broadcast({"type": "update", "data": log_entry})

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
            try:
                method = item['method']
                path_pattern = item['path_pattern']
                status = item['status']
                latency = item['latency']
                resp_body = item['response_body']
                req_body = item['request_body']
                
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
                
                # Learn Schema (Only if it's JSON)
                if status < 300 and resp_body and isinstance(resp_body, (dict, list)):
                    behavior.response_schema = learn_schema(behavior.response_schema, resp_body)
                
                if req_body and isinstance(req_body, (dict, list)):
                    behavior.request_schema = learn_schema(behavior.request_schema, req_body)
                
                session.add(behavior)
            except Exception as e:
                logger.error(f"❌ Error learning from request: {str(e)}")
                continue
        
        try:
            await session.commit()
        except Exception as e:
            logger.error(f"❌ Error committing learning batch: {str(e)}")

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
                "schema_preview": behavior.response_schema,
                "request_schema": behavior.request_schema
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

@app.post("/admin/endpoints/{endpoint_id}/schema")
async def update_endpoint_schema(endpoint_id: int, data: Dict[str, Any]):
    schema = data.get("schema")
    schema_type = data.get("type", "outbound") # 'inbound' or 'outbound'
    
    async with AsyncSessionLocal() as session:
        update_vals = {}
        if schema_type == "inbound":
            update_vals["request_schema"] = schema
        else:
            update_vals["response_schema"] = schema
            
        await session.execute(
            update(EndpointBehavior)
            .where(EndpointBehavior.endpoint_id == endpoint_id)
            .values(**update_vals)
        )
        await session.commit()
        return {"status": "schema_updated", "type": schema_type}

@app.get("/admin/export-openapi")
async def export_openapi():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Endpoint))
        endpoints = res.scalars().all()
        
        paths = {}
        for ep in endpoints:
            b_res = await session.execute(select(EndpointBehavior).where(EndpointBehavior.endpoint_id == ep.id))
            behavior = b_res.scalars().first()
            
            p = ep.path_pattern
            m = ep.method.lower()
            
            if p not in paths: paths[p] = {}
            
            # Extract Path Parameters from {id}, {name}, etc.
            path_params = re.findall(r'\{(.*?)\}', p)
            parameters = []
            for param in path_params:
                parameters.append({
                    "name": param,
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"}
                })
            
            paths[p][m] = {
                "summary": f"Inferred {ep.method} for {p}",
                "parameters": parameters,
                "responses": {
                    "200": {
                        "description": "Learned Success Response",
                        "content": {
                            "application/json": {
                                "example": behavior.response_schema
                            }
                        }
                    }
                }
            }
            
            if behavior.request_schema and m in ['post', 'put', 'patch', 'delete']:
                paths[p][m]["requestBody"] = {
                    "content": {
                        "application/json": {
                            "example": behavior.request_schema
                        }
                    }
                }
            
        return {
            "openapi": "3.0.0",
            "info": {
                "title": "AI Learned API Contract", 
                "version": "1.0.0",
                "description": "This contract was automatically generated by observing real production traffic."
            },
            "servers": [{"url": "/", "description": "Local Mock Platform"}],
            "paths": paths
        }

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def catch_all(request: Request, path: str, background_tasks: BackgroundTasks):
    method = request.method
    normalized = normalize_path(f"/{path}")
    
    # Mode Selection: Header > Global State
    mock_header = request.headers.get("X-Mock-Enabled")
    if mock_header:
        mock_enabled = mock_header.lower() == "true"
    else:
        mock_enabled = PLATFORM_STATE["mode"] == "mock"
    
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

    # 1. MOCK MODE (Explicit)
    if mock_enabled:
        return await generate_endpoint_mock(behavior, chaos, normalized, request)

    # 2. PROXY MODE (With Automatic Mock Fallback)
    target_full_url = f"{TARGET_URL}/{path}"
    start_time = time.time()
    
    # Pre-read request body for learning
    req_body_bytes = await request.body()
    try:
        req_body_json = await request.json() if req_body_bytes else None
    except Exception:
        req_body_json = None # Non-JSON body

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
            proxy_resp = await client.request(
                method=method,
                url=target_full_url,
                headers=headers,
                params=dict(request.query_params),
                content=req_body_bytes,
                follow_redirects=False
            )
            
        latency_ms = (time.time() - start_time) * 1000
        
        # Try to parse response JSON for learning, but don't fail if it's not JSON
        try:
            resp_body_json = proxy_resp.json()
        except Exception:
            resp_body_json = None # HTML, Text, etc.
            
        if PLATFORM_STATE["learning_enabled"]:
            async with buffer_lock:
                LEARNING_BUFFER.append({
                    "method": method, "path_pattern": normalized,
                    "status": proxy_resp.status_code, "latency": latency_ms, 
                    "response_body": resp_body_json,
                    "request_body": req_body_json
                })
                background_tasks.add_task(process_learning_buffer)
        
        await add_to_logs(method, normalized, proxy_resp.status_code, latency_ms, "Proxy")

        return Response(
            content=proxy_resp.content,
            status_code=proxy_resp.status_code,
            headers=dict(proxy_resp.headers)
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
        # AUTOMATIC FAILOVER: Backend is down, serve a mock instead!
        logger.warning(f"⚠️ PROXY FAILOVER: Backend {TARGET_URL} unreachable. Error: {str(e)}")
        return await generate_endpoint_mock(behavior, chaos, normalized, request, is_failover=True)
    except Exception as e:
        logger.error(f"💥 UNEXPECTED PROXY ERROR: {str(e)}")
        raise HTTPException(status_code=502, detail=f"Proxy Error: {str(e)}")

async def generate_endpoint_mock(behavior, chaos, normalized, request, is_failover=False):
    try:
        # Apply Chaos
        effective_chaos = chaos.chaos_level if chaos and chaos.is_active else 0
        header_chaos = request.headers.get("X-Chaos-Level")
        if header_chaos:
            try:
                effective_chaos = int(header_chaos)
            except: pass

        # Decide Error vs Success
        # error_rate is from learned behavior, chaos is artificial
        error_prob = behavior.error_rate if behavior else 0
        error_prob += (effective_chaos / 100.0)
        
        if random.random() < min(error_prob, 0.9):
            log_status = 500
            await add_to_logs(request.method, normalized, log_status, 0, "Mock")
            return JSONResponse(
                content={"error": "Status Injected (AI/Chaos)", "endpoint": normalized, "failover": is_failover},
                status_code=log_status
            )

        # Simulate Latency
        latency = (behavior.latency_mean if behavior else 50) + (effective_chaos * 10)
        await asyncio.sleep(latency / 1000.0)
        
        # Choose Status Code
        status_code = 200
        if behavior and behavior.status_code_distribution:
            codes = list(behavior.status_code_distribution.keys())
            probs = list(behavior.status_code_distribution.values())
            try:
                status_code = int(random.choices(codes, weights=probs)[0])
            except:
                status_code = 200
        
        # Generate Body
        try:
            req_body = await request.json()
        except:
            req_body = {}
            
        schema = behavior.response_schema if behavior else None
        mock_body = generate_mock_response(schema, req_body)
        
        if not mock_body:
            mock_body = {"message": "AI fallback (No patterns learned yet)", "endpoint": normalized}
            
        if isinstance(mock_body, dict) and is_failover:
            mock_body["_meta"] = "Generated via AI Fallback (Backend Unreachable)"
        
        # Log it
        await add_to_logs(request.method, normalized, status_code, latency, "Mock")
            
        return JSONResponse(content=mock_body, status_code=status_code)
    except Exception as e:
        logger.error(f"❌ MOCK GENERATION FAILED: {str(e)}")
        return JSONResponse(
            content={"error": "Mock Generation Failed", "detail": str(e)},
            status_code=500
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
