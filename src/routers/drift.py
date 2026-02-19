"""
Drift Router
==============
Contract drift alert management: list, resolve, per-endpoint stats.
"""

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update

from core.database import AsyncSessionLocal
from core.models import ContractDrift

logger = logging.getLogger("mock_platform")

router = APIRouter()


@router.get("/admin/drift-alerts")
async def get_drift_alerts(endpoint_id: Optional[int] = None, unresolved_only: bool = False):
    """
    Get all drift alerts, optionally filtered by endpoint or resolution status.
    """
    async with AsyncSessionLocal() as session:
        query = select(ContractDrift).order_by(ContractDrift.detected_at.desc())

        if endpoint_id:
            query = query.where(ContractDrift.endpoint_id == endpoint_id)

        if unresolved_only:
            query = query.where(ContractDrift.is_resolved == False)

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


@router.post("/admin/drift-alerts/{alert_id}/resolve")
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
                .values(is_resolved=True, resolved_at=datetime.datetime.utcnow())
            )
            await session.commit()
            logger.info(f"✅ Alert {alert_id} marked as resolved")
            return {"status": "resolved"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error resolving alert {alert_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/endpoints/{endpoint_id}/drift-stats")
async def get_endpoint_drift_stats(endpoint_id: int):
    """
    Get drift statistics for a specific endpoint.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ContractDrift)
            .where(ContractDrift.endpoint_id == endpoint_id)
            .order_by(ContractDrift.detected_at.desc())
        )
        all_alerts = result.scalars().all()

        unresolved_alerts = [a for a in all_alerts if not a.is_resolved]
        unresolved_count = len(unresolved_alerts)

        latest_alert = unresolved_alerts[0] if unresolved_alerts else (all_alerts[0] if all_alerts else None)

        avg_score = sum(a.drift_score for a in all_alerts) / len(all_alerts) if all_alerts else 0

        return {
            "total_alerts": len(all_alerts),
            "unresolved_alerts": unresolved_count,
            "average_drift_score": round(avg_score, 2),
            "latest_alert": {
                "id": latest_alert.id,
                "detected_at": latest_alert.detected_at.isoformat(),
                "drift_score": latest_alert.drift_score,
                "drift_summary": latest_alert.drift_summary,
                "drift_details": latest_alert.drift_details
            } if latest_alert else None
        }
