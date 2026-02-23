"""
Endpoints Router
=================
CRUD and management for learned endpoints: list, stats, chaos config, schema updates.
"""

import re
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update

from core.database import AsyncSessionLocal
from core.models import Endpoint, EndpointBehavior, ChaosConfig
from utils.normalization import normalize_path
from utils.schema_learner import learn_schema

router = APIRouter()


@router.post("/admin/endpoints/manual")
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
                behavior.status_code_distribution = {str(status_code): 1.0}
            if behavior and request_body:
                behavior.request_schema = learn_schema(behavior.request_schema, request_body)

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


@router.get("/admin/endpoints")
async def list_endpoints():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Endpoint))
        endpoints = res.scalars().all()
        return endpoints


@router.get("/admin/endpoints/{endpoint_id}/stats")
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


@router.post("/admin/endpoints/{endpoint_id}/chaos")
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


@router.post("/admin/endpoints/{endpoint_id}/schema")
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


