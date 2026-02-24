"""
Dashboard Router
=================
Static page serving, platform config, chaos profiles, learning/mode toggles,
recent logs, and WebSocket endpoint.
"""

import os

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy import update

from core.database import AsyncSessionLocal
import core.state as state
from core.state import PLATFORM_STATE, CHAOS_PROFILES, RECENT_LOGS, logs_lock
from core.websocket import manager
from core.models import ChaosConfig, Endpoint, EndpointBehavior
import re

router = APIRouter()

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "static")


# ── Static Pages ──

@router.get("/")
async def get_landing():
    landing_path = os.path.join(STATIC_DIR, "landing.html")
    if os.path.exists(landing_path):
        return FileResponse(landing_path)
    return JSONResponse({"error": "Landing landing.html not found"}, status_code=404)


@router.get("/admin/dashboard")
async def get_dashboard():
    dashboard_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(dashboard_path):
        return FileResponse(dashboard_path)
    return JSONResponse({"error": "Dashboard index.html not found"}, status_code=404)


@router.get("/admin/explorer")
async def get_explorer():
    explorer_path = os.path.join(STATIC_DIR, "explorer.html")
    if os.path.exists(explorer_path):
        return FileResponse(explorer_path)
    return JSONResponse({"error": "Explorer explorer.html not found"}, status_code=404)


@router.get("/admin/docs")
async def get_swagger_ui():
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(
        openapi_url="/admin/export-openapi",
        title="AI Truth Swagger",
        swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png"
    )


@router.get("/admin/guide")
async def get_user_guide():
    guide_path = os.path.join(STATIC_DIR, "docs.html")
    if os.path.exists(guide_path):
        return FileResponse(guide_path)
    return JSONResponse({"error": "docs.html not found"}, status_code=404)


# ── Config & State ──

@router.get("/admin/config")
async def get_config():
    return {
        "chaos_level": 0,
        "learning_mode": PLATFORM_STATE["learning_enabled"],
        "platform_mode": PLATFORM_STATE["mode"],
        "target_url": state.TARGET_URL,
        "active_chaos_profile": PLATFORM_STATE["active_chaos_profile"]
    }


@router.post("/admin/target")
async def set_target_url(request: Request):
    """Change the proxy target URL at runtime."""
    data = await request.json()
    new_url = data.get("target_url", "").strip().rstrip("/")
    if not new_url or not new_url.startswith(("http://", "https://")):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid URL. Must start with http:// or https://")
    state.TARGET_URL = new_url
    return {"status": "success", "target_url": state.TARGET_URL}


@router.get("/admin/chaos/profiles")
async def get_chaos_profiles():
    return CHAOS_PROFILES


@router.post("/admin/chaos/profiles")
async def set_active_chaos_profile(request: Request):
    data = await request.json()
    profile = data.get("profile", "normal")
    if profile in CHAOS_PROFILES:
        PLATFORM_STATE["active_chaos_profile"] = profile
        return {"status": "profile_applied", "profile": profile}
    from fastapi import HTTPException
    raise HTTPException(status_code=400, detail="Invalid profile")


@router.post("/admin/chaos")
async def set_chaos_globally(request: Request):
    data = await request.json()
    level = data.get("level", 0)
    async with AsyncSessionLocal() as session:
        await session.execute(update(ChaosConfig).values(chaos_level=level, is_active=True))
        await session.commit()
    return {"status": "updated_globally", "level": level}


@router.post("/admin/learning")
async def toggle_learning(request: Request):
    data = await request.json()
    PLATFORM_STATE["learning_enabled"] = data.get("enabled", True)
    return {"status": "success", "learning_enabled": PLATFORM_STATE["learning_enabled"]}


@router.post("/admin/mode")
async def set_platform_mode(request: Request):
    data = await request.json()
    PLATFORM_STATE["mode"] = data.get("mode", "proxy")
    return {"status": "success", "mode": PLATFORM_STATE["mode"]}


@router.get("/admin/logs")
async def get_recent_logs():
    async with logs_lock:
        return RECENT_LOGS


# ── WebSocket ──

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        async with logs_lock:
            await websocket.send_json({"type": "initial", "data": RECENT_LOGS})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.get("/admin/export-openapi")
async def export_openapi():
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        res = await session.execute(select(Endpoint))
        endpoints = res.scalars().all()

        paths = {}
        for ep in endpoints:
            b_res = await session.execute(
                select(EndpointBehavior).where(EndpointBehavior.endpoint_id == ep.id)
            )
            behavior = b_res.scalars().first()
            if not behavior:
                continue

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

            # Generate Responses based on learned status code distribution
            responses = {}
            if behavior.status_code_distribution:
                for code, prob in behavior.status_code_distribution.items():
                    # Only include success codes in the main documentation
                    if int(code) < 400:
                        responses[code] = {
                            "description": f"Learned Response (Occurs {prob*100:.0f}% of cases)",
                            "content": {
                                "application/json": {
                                    "example": behavior.response_schema
                                }
                            }
                        }
            
            # Fallback if no distribution learned yet
            if not responses:
                responses["200"] = {
                    "description": "Learned Success Response",
                    "content": {
                        "application/json": {
                            "example": behavior.response_schema
                        }
                    }
                }

            paths[p][m] = {
                "summary": f"Inferred {ep.method} for {p}",
                "parameters": parameters,
                "responses": responses
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
            "servers": [{"url": "/", "description": "AI Mock Platform"}],
            "paths": paths
        }
