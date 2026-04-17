"""
Explorer Router
================
Consolidated endpoint for the Explorer page with pagination, search, and health.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select, func

from core.database import AsyncSessionLocal
from core.state import health_monitor
from core.models import Endpoint, EndpointBehavior, ContractDrift
from utils.drift_detector import narrate_drift
from core.auth import require_auth

router = APIRouter()


@router.get("/admin/explorer/overview", dependencies=[Depends(require_auth)])
async def get_explorer_overview(
    search: Optional[str] = None, 
    limit: int = 10, 
    offset: int = 0,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    q: Optional[str] = None
):
    """
    Consolidated endpoint for the Explorer page with pagination and search.
    Supports both limit/offset (API style) and page/page_size (UI style).
    """
    # Map UI params to API params if present
    if q: search = q
    if page_size: limit = page_size
    if page and page_size: offset = (page - 1) * page_size
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
        count_res = await session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total_count = count_res.scalar() or 0

        # Apply ordering and pagination
        query = query.order_by(Endpoint.id.desc()).limit(limit).offset(offset)
        res = await session.execute(query)
        endpoints = res.scalars().all()

        result_data = []
        for ep in endpoints:
            try:
                # 1. Get stats (Behavior)
                b_res = await session.execute(
                    select(EndpointBehavior).where(EndpointBehavior.endpoint_id == ep.id)
                )
                behavior = b_res.scalars().first()
                
                # 2. Get latest unresolved drift alert
                d_res = await session.execute(
                    select(ContractDrift)
                    .where(ContractDrift.endpoint_id == ep.id, ContractDrift.is_resolved.is_(False))
                    .order_by(ContractDrift.detected_at.desc())
                )
                unresolved_alerts = d_res.scalars().all()
                latest_alert = unresolved_alerts[0] if unresolved_alerts else None
                
                # Format drift
                drift_data = None
                if latest_alert:
                    # Defensive timestamp handling
                    try:
                        detected_str = latest_alert.detected_at.isoformat() if hasattr(latest_alert.detected_at, "isoformat") else str(latest_alert.detected_at)
                    except:
                        detected_str = str(latest_alert.detected_at)
                    
                    # Ensure details is a list of dicts with compatible keys (type, message)
                    details_raw = latest_alert.drift_details
                    if isinstance(details_raw, str):
                        import json
                        try: details_raw = json.loads(details_raw)
                        except: details_raw = []
                    
                    if not isinstance(details_raw, list):
                        details_raw = []
                    
                    # Normalize keys for the frontend JS (d.type, d.message)
                    details = []
                    for d in details_raw:
                        if not isinstance(d, dict): continue
                        # Map change_type -> type and explanation -> message
                        details.append({
                            "type": d.get("change_type", d.get("type", "unknown")),
                            "path": d.get("path", "$"),
                            "message": d.get("explanation", d.get("message", "Contract drift detected")),
                            "severity": d.get("severity", "low").lower()
                        })
                        
                    drift_data = {
                        "id": latest_alert.id,
                        "detected_at": detected_str,
                        "drift_score": latest_alert.drift_score or 0.0,
                        "drift_summary": latest_alert.drift_summary or "Drift Detected",
                        "drift_details": details,
                        "drift_narration": latest_alert.drift_narration or narrate_drift(details, endpoint_path=ep.path_pattern)
                    }

                # 3. Build response item
                result_data.append({
                    "id": ep.id,
                    "method": ep.method,
                    "path_pattern": ep.path_pattern,
                    "health": health_monitor.get_endpoint_health(ep.id),
                    "stats": {
                        "latency_mean": behavior.latency_mean if behavior else 0,
                        "error_rate": behavior.error_rate if behavior else 0,
                        "status_codes": (behavior.status_code_distribution if behavior else {}) or {},
                        "request_schema": (behavior.request_schema if behavior else {}) or {},
                        "schema_preview": (behavior.response_schema if behavior else {}) or {}
                    },
                    "latest_drift": drift_data,
                    "unresolved_count": len(unresolved_alerts)
                })
            except Exception as e:
                # If one endpoint fails, log it and skip but don't crash the whole list
                import logging
                logging.error(f"Error processing endpoint {ep.id} in explorer: {e}")
                continue

        return {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "items": result_data
        }
