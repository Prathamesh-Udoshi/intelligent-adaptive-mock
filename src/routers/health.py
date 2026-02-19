"""
Health Router
==============
AI anomaly detection health monitoring endpoints.
"""

from fastapi import APIRouter
from sqlalchemy import select

from core.database import AsyncSessionLocal
from core.state import health_monitor
from core.models import HealthMetric

router = APIRouter()


@router.get("/admin/health")
async def get_all_health():
    """
    Returns health data for all monitored endpoints.
    """
    return {
        "global": health_monitor.get_global_health(),
        "endpoints": health_monitor.get_all_endpoint_health()
    }


@router.get("/admin/health/global")
async def get_global_health():
    """
    Returns the aggregated platform health score.
    """
    return health_monitor.get_global_health()


@router.get("/admin/health/{endpoint_id}")
async def get_endpoint_health(endpoint_id: int):
    """
    Returns health data for a specific endpoint.
    """
    health = health_monitor.get_endpoint_health(endpoint_id)

    # Also get recent health metrics from DB for historical context
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HealthMetric)
            .where(HealthMetric.endpoint_id == endpoint_id)
            .order_by(HealthMetric.recorded_at.desc())
            .limit(20)
        )
        recent_metrics = result.scalars().all()

    return {
        "current": health,
        "history": [
            {
                "recorded_at": m.recorded_at.isoformat(),
                "latency_ms": m.latency_ms,
                "status_code": m.status_code,
                "health_score": m.health_score,
                "latency_anomaly": m.latency_anomaly,
                "error_spike": m.error_spike,
                "size_anomaly": m.size_anomaly,
                "anomaly_reasons": m.anomaly_reasons
            }
            for m in recent_metrics
        ]
    }
