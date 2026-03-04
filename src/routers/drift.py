"""
Drift Router
==============
Contract drift alert management: list, resolve, per-endpoint stats.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update, func

from core.database import AsyncSessionLocal
from core.models import ContractDrift
from core.auth import require_auth

logger = logging.getLogger("mock_platform")

router = APIRouter()


@router.get("/admin/drift-alerts", dependencies=[Depends(require_auth)])
async def get_drift_alerts(
    endpoint_id: Optional[int] = None,
    unresolved_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200, description="Max alerts to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
):
    """
    Get drift alerts with pagination, optionally filtered by endpoint or resolution status.
    """
    async with AsyncSessionLocal() as session:
        query = select(ContractDrift).order_by(ContractDrift.detected_at.desc())

        if endpoint_id:
            query = query.where(ContractDrift.endpoint_id == endpoint_id)

        if unresolved_only:
            query = query.where(ContractDrift.is_resolved.is_(False))

        # Paginate
        query = query.limit(limit).offset(offset)
        result = await session.execute(query)
        alerts = result.scalars().all()

        return [{
            "id": alert.id,
            "endpoint_id": alert.endpoint_id,
            "detected_at": alert.detected_at.isoformat(),
            "drift_score": alert.drift_score,
            "drift_summary": alert.drift_summary,
            "drift_details": alert.drift_details,
            "is_resolved": alert.is_resolved,
            "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None
        } for alert in alerts]


@router.post("/admin/drift-alerts/{alert_id}/resolve", dependencies=[Depends(require_auth)])
async def resolve_drift_alert(alert_id: int):
    """
    Mark a drift alert as resolved.
    """
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(ContractDrift).where(ContractDrift.id == alert_id))
            alert = res.scalars().first()
            if not alert:
                logger.error(f"❌ Alert {alert_id} not found for resolution")
                raise HTTPException(status_code=404, detail="Alert not found")

            await session.execute(
                update(ContractDrift)
                .where(ContractDrift.id == alert_id)
                .values(is_resolved=True, resolved_at=func.now())
            )
            await session.commit()
            logger.info(f"✅ Alert {alert_id} marked as resolved")
            return {"status": "resolved"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error resolving alert {alert_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/endpoints/{endpoint_id}/drift-stats", dependencies=[Depends(require_auth)])
async def get_endpoint_drift_stats(endpoint_id: int):
    """
    Get drift statistics for a specific endpoint.
    """
    async with AsyncSessionLocal() as session:
        # Use SQL aggregation instead of loading all rows into Python
        count_res = await session.execute(
            select(func.count(ContractDrift.id))
            .where(ContractDrift.endpoint_id == endpoint_id)
        )
        total_count = count_res.scalar() or 0

        unresolved_res = await session.execute(
            select(func.count(ContractDrift.id))
            .where(
                ContractDrift.endpoint_id == endpoint_id,
                ContractDrift.is_resolved.is_(False),
            )
        )
        unresolved_count = unresolved_res.scalar() or 0

        avg_res = await session.execute(
            select(func.avg(ContractDrift.drift_score))
            .where(ContractDrift.endpoint_id == endpoint_id)
        )
        avg_score = avg_res.scalar() or 0.0

        # Get the latest alert (prefer unresolved)
        latest_res = await session.execute(
            select(ContractDrift)
            .where(ContractDrift.endpoint_id == endpoint_id)
            .order_by(
                ContractDrift.is_resolved.asc(),  # unresolved first
                ContractDrift.detected_at.desc(),
            )
            .limit(1)
        )
        latest_alert = latest_res.scalars().first()

        return {
            "total_alerts": total_count,
            "unresolved_alerts": unresolved_count,
            "average_drift_score": round(float(avg_score), 2),
            "latest_alert": {
                "id": latest_alert.id,
                "detected_at": latest_alert.detected_at.isoformat() if latest_alert.detected_at else None,
                "drift_score": latest_alert.drift_score,
                "drift_summary": latest_alert.drift_summary,
                "drift_details": latest_alert.drift_details,
            } if latest_alert else None,
        }
