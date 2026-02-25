"""
Export Router
==============
Type export endpoints: TypeScript, Pydantic, JSON Schema.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from core.database import AsyncSessionLocal
from core.models import Endpoint, EndpointBehavior
from utils.type_exporter import export_all_typescript, export_all_pydantic, export_all_json_schema
from core.auth import require_auth

router = APIRouter()


@router.get("/admin/export-types", dependencies=[Depends(require_auth)])
async def export_types(format: str = "typescript"):
    """
    Auto-generate client type definitions from learned API schemas.

    Query params:
      - format: 'typescript' | 'pydantic' | 'jsonschema' (default: typescript)

    Returns TypeScript interfaces, Pydantic models, or JSON Schema documents
    derived from all learned endpoint schemas.
    """
    allowed_formats = ["typescript", "pydantic", "jsonschema"]
    if format.lower() not in allowed_formats:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format '{format}'. Allowed: {', '.join(allowed_formats)}"
        )

    # Gather all endpoints with their schemas
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Endpoint).order_by(Endpoint.id)
        )
        endpoints = result.scalars().all()

        endpoint_data = []
        for ep in endpoints:
            b_res = await session.execute(
                select(EndpointBehavior).where(EndpointBehavior.endpoint_id == ep.id)
            )
            behavior = b_res.scalars().first()

            if behavior and (behavior.response_schema or behavior.request_schema):
                endpoint_data.append({
                    "method": ep.method,
                    "path_pattern": ep.path_pattern,
                    "request_schema": behavior.request_schema,
                    "response_schema": behavior.response_schema
                })

    if not endpoint_data:
        raise HTTPException(
            status_code=404,
            detail="No learned schemas found. Send some traffic in proxy mode first to learn API schemas."
        )

    fmt = format.lower()
    if fmt == "typescript":
        content = export_all_typescript(endpoint_data)
        return Response(
            content=content,
            media_type="text/plain",
            headers={"Content-Disposition": "inline; filename=api-types.ts"}
        )
    elif fmt == "pydantic":
        content = export_all_pydantic(endpoint_data)
        return Response(
            content=content,
            media_type="text/plain",
            headers={"Content-Disposition": "inline; filename=api_models.py"}
        )
    else:  # jsonschema
        content = export_all_json_schema(endpoint_data)
        return JSONResponse(content=content)
