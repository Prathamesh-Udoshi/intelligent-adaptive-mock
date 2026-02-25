"""
Health Router
==============
AI anomaly detection health monitoring endpoints.
"""

from fastapi import APIRouter, HTTPException

from sqlalchemy import select

from core.database import AsyncSessionLocal
from core.state import health_monitor, adaptive_detector
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


@router.post("/admin/detector/reset/{path:path}")
async def reset_endpoint_stats(path: str):
    """
    Resets the learned latency baseline for a single endpoint.

    Use this when an endpoint's training data is contaminated (e.g.
    it was tested under chaos conditions and its 'normal' mean is now wrong).

    Args:
        path: The URL path of the endpoint to reset (e.g. /health or /analyze).
    """
    full_path = "/" + path.lstrip("/")
    if full_path not in adaptive_detector.endpoint_stats:
        raise HTTPException(
            status_code=404,
            detail=f"No learned stats found for '{full_path}'. Known: {list(adaptive_detector.endpoint_stats.keys())}"
        )
    del adaptive_detector.endpoint_stats[full_path]
    adaptive_detector.flush()
    return {
        "status": "reset",
        "endpoint": full_path,
        "message": f"Baseline for '{full_path}' cleared. It will re-learn from new traffic."
    }


@router.post("/admin/detector/reset-all")
async def reset_all_stats():
    """
    Wipes ALL learned latency baselines across every endpoint.

    Use this when the platform was trained on contaminated traffic
    (e.g. during chaos testing or first-run anomalies).
    """
    count = len(adaptive_detector.endpoint_stats)
    adaptive_detector.endpoint_stats.clear()
    adaptive_detector.flush()
    return {
        "status": "reset",
        "endpoints_cleared": count,
        "message": "All baselines wiped. The AI will re-learn from fresh traffic."
    }
