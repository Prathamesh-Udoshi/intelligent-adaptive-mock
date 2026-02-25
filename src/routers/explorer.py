"""
Explorer Router
================
Consolidated endpoint for the Explorer page with pagination, search, and health.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select, text

from core.database import AsyncSessionLocal
from core.state import health_monitor
from core.models import Endpoint, EndpointBehavior, ContractDrift
from utils.drift_detector import narrate_drift
from core.auth import require_auth

router = APIRouter()


@router.get("/admin/explorer/overview", dependencies=[Depends(require_auth)])
async def get_explorer_overview(search: Optional[str] = None, limit: int = 10, offset: int = 0):
    """
    Consolidated endpoint for the Explorer page with pagination and search.
    Returns a subset of endpoints with stats and drift alerts.
    """
    async with AsyncSessionLocal() as session:
        # Build base query
        query = select(Endpoint)
        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                (Endpoint.path_pattern.like(search_pattern)) |
                (Endpoint.method.like(search_pattern.upper()))
            )

        # Get total count for pagination UI
        count_res = await session.execute(select(text("count(*)")).select_from(query.subquery()))
        total_count = count_res.scalar()

        # Apply ordering and pagination
        query = query.order_by(Endpoint.id.desc()).limit(limit).offset(offset)
        res = await session.execute(query)
        endpoints = res.scalars().all()

        result_data = []
        for ep in endpoints:
            # Get stats
            b_res = await session.execute(select(EndpointBehavior).where(EndpointBehavior.endpoint_id == ep.id))
            behavior = b_res.scalars().first()

            # Get drift alerts
            d_res = await session.execute(
                select(ContractDrift)
                .where(ContractDrift.endpoint_id == ep.id, ContractDrift.is_resolved == False)
                .order_by(ContractDrift.detected_at.desc())
            )
            unresolved_alerts = d_res.scalars().all()

            result_data.append({
                "id": ep.id,
                "method": ep.method,
                "path_pattern": ep.path_pattern,
                "health": health_monitor.get_endpoint_health(ep.id),
                "stats": {
                    "latency_mean": behavior.latency_mean if behavior else 0,
                    "error_rate": behavior.error_rate if behavior else 0,
                    "status_codes": behavior.status_code_distribution if behavior else {},
                    "request_schema": behavior.request_schema if behavior else {},
                    "schema_preview": behavior.response_schema if behavior else {}
                } if behavior else None,
                "latest_drift": {
                    "id": unresolved_alerts[0].id,
                    "detected_at": unresolved_alerts[0].detected_at.isoformat(),
                    "drift_score": unresolved_alerts[0].drift_score,
                    "drift_summary": unresolved_alerts[0].drift_summary,
                    "drift_details": unresolved_alerts[0].drift_details,
                    "drift_narration": narrate_drift(
                        unresolved_alerts[0].drift_details if isinstance(unresolved_alerts[0].drift_details, list) else [],
                        endpoint_path=ep.path_pattern
                    )
                } if unresolved_alerts else None,
                "unresolved_count": len(unresolved_alerts)
            })

        return {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "items": result_data
        }
