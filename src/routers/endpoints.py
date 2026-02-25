"""
Endpoints Router
=================
CRUD and management for learned endpoints: list, stats, chaos config, schema updates.
"""

import re
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import flag_modified

from core.database import AsyncSessionLocal
from core.models import Endpoint, EndpointBehavior, ChaosConfig
from utils.normalization import normalize_path
from utils.schema_learner import learn_schema
from core.auth import require_auth

router = APIRouter()


@router.post("/admin/endpoints/manual", dependencies=[Depends(require_auth)])
async def create_manual_endpoint(request: Request):
    """
    Manually define an endpoint spec when the real backend isn't built yet.

    Body:
        method: str         — HTTP method (GET, POST, PUT, etc.)
        path: str           — URL path pattern (e.g. /users/{id}/profile)
        status_code: int    — Expected status code (default: 200)
        response_body: dict — Sample JSON response (used to learn/generate mocks)
        request_body: dict  — Optional sample JSON request body
    """
    data = await request.json()
    method = data.get("method", "GET").upper()
    path = data.get("path", "").strip()
    status_code = data.get("status_code", 200)
    response_body = data.get("response_body")
    request_body = data.get("request_body")

    if not path:
        raise HTTPException(status_code=400, detail="Path is required")
    if not path.startswith("/"):
        path = "/" + path

    # Normalize but preserve user-provided {param} patterns
    normalized = normalize_path(path)

    async with AsyncSessionLocal() as session:
        # Check if endpoint already exists
        existing = await session.execute(
            select(Endpoint).where(Endpoint.method == method, Endpoint.path_pattern == normalized)
        )
        endpoint = existing.scalars().first()

        if endpoint:
            # Update existing endpoint's schemas
            behavior_res = await session.execute(
                select(EndpointBehavior).where(EndpointBehavior.endpoint_id == endpoint.id)
            )
            behavior = behavior_res.scalars().first()

            if behavior and response_body:
                behavior.response_schema = learn_schema(behavior.response_schema, response_body)
                flag_modified(behavior, "response_schema")
                behavior.status_code_distribution = {str(status_code): 1.0}
                flag_modified(behavior, "status_code_distribution")
            if behavior and request_body:
                behavior.request_schema = learn_schema(behavior.request_schema, request_body)
                flag_modified(behavior, "request_schema")

            session.add(behavior)
            await session.commit()
            return {"status": "updated", "id": endpoint.id, "method": method, "path": normalized}
        else:
            # Create new endpoint + behavior + chaos config
            endpoint = Endpoint(method=method, path_pattern=normalized, target_url="manual://user-defined")
            session.add(endpoint)
            await session.flush()

            # Learn schema from the sample response
            resp_schema = learn_schema(None, response_body) if response_body else None
            req_schema = learn_schema(None, request_body) if request_body else None

            behavior = EndpointBehavior(
                endpoint_id=endpoint.id,
                latency_mean=50.0,
                latency_std=10.0,
                error_rate=0.0,
                status_code_distribution={str(status_code): 1.0},
                response_schema=resp_schema,
                request_schema=req_schema
            )
            chaos = ChaosConfig(endpoint_id=endpoint.id)
            session.add(behavior)
            session.add(chaos)
            await session.commit()

            return {"status": "created", "id": endpoint.id, "method": method, "path": normalized}


@router.get("/admin/endpoints", dependencies=[Depends(require_auth)])
async def list_endpoints():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Endpoint))
        endpoints = res.scalars().all()
        return endpoints


@router.get("/admin/endpoints/{endpoint_id}/stats", dependencies=[Depends(require_auth)])
async def get_endpoint_stats(endpoint_id: int):
    async with AsyncSessionLocal() as session:
        b_res = await session.execute(select(EndpointBehavior).where(EndpointBehavior.endpoint_id == endpoint_id))
        behavior = b_res.scalars().first()
        c_res = await session.execute(select(ChaosConfig).where(ChaosConfig.endpoint_id == endpoint_id))
        chaos = c_res.scalars().first()

        # Add real-time adaptive stats from the AI Brain
        from core.state import adaptive_detector, health_monitor
        from core.models import Endpoint
        
        ep_res = await session.execute(select(Endpoint).where(Endpoint.id == endpoint_id))
        endpoint = ep_res.scalars().first()
        
        adaptive_stats = {}
        if endpoint:
            adaptive_stats = adaptive_detector.get_stats(endpoint.path_pattern)
            # Add dynamic threshold
            adaptive_stats["dynamic_threshold"] = adaptive_detector._get_dynamic_threshold(
                adaptive_stats.get("mean", 0), 
                adaptive_stats.get("std", 0)
            )
            
        health = health_monitor.get_endpoint_health(endpoint_id)

        return {
            "behavior": {
                "latency_mean": behavior.latency_mean,
                "error_rate": behavior.error_rate,
                "status_codes": behavior.status_code_distribution,
                "schema_preview": behavior.response_schema,
                "request_schema": behavior.request_schema,
                "adaptive_stats": adaptive_stats,
                "health": health
            },
            "chaos": {
                "level": chaos.chaos_level,
                "active": chaos.is_active
            }
        }


@router.post("/admin/endpoints/{endpoint_id}/chaos", dependencies=[Depends(require_auth)])
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


@router.post("/admin/endpoints/{endpoint_id}/schema", dependencies=[Depends(require_auth)])
async def update_endpoint_schema(endpoint_id: int, data: Dict[str, Any]):
    schema = data.get("schema")
    schema_type = data.get("type", "outbound")  # 'inbound' or 'outbound'

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


