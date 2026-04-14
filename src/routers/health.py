"""
Health Router
==============
AI anomaly detection health monitoring endpoints.

Provides:
  - GET  /admin/health                   — Global + all-endpoint health overview
  - GET  /admin/health/global            — Aggregated platform health
  - GET  /admin/health/{endpoint_id}     — Per-endpoint health + history
  - POST /admin/detector/reset/{path}    — Reset learned baseline for one endpoint
  - POST /admin/detector/reset-all       — Wipe all learned baselines
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func as sa_func

from core.database import AsyncSessionLocal
from core.state import health_monitor, adaptive_detector, lstm_predictor
from core.models import HealthMetric, Endpoint
from core.auth import require_auth

logger = logging.getLogger("mock_platform")

router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _derive_status(score: float) -> str:
    """Derive status label from a numeric health score."""
    if score >= 80:
        return "healthy"
    elif score >= 50:
        return "degraded"
    return "critical"


def _compute_history_stats(metrics: list) -> dict:
    """Compute summary statistics from a list of HealthMetric rows."""
    if not metrics:
        return {
            "avg_latency": 0.0,
            "error_rate": 0.0,
            "anomaly_rate": 0.0,
            "total_requests": 0,
            "avg_health_score": 100.0,
            "status": "healthy",
        }

    total = len(metrics)
    avg_latency = sum(m.latency_ms for m in metrics) / total
    error_count = sum(1 for m in metrics if m.is_error)
    anomaly_count = sum(1 for m in metrics if m.latency_anomaly or m.error_spike or m.size_anomaly)
    avg_score = sum(m.health_score for m in metrics) / total

    return {
        "avg_latency": round(avg_latency, 1),
        "error_rate": round(error_count / total, 4),
        "anomaly_rate": round(anomaly_count / total, 4),
        "total_requests": total,
        "avg_health_score": round(avg_score, 1),
        "status": _derive_status(avg_score),
    }


def _format_metric(m: HealthMetric) -> dict:
    """Serialize a HealthMetric ORM row to a dashboard-friendly dict."""
    return {
        "recorded_at": m.recorded_at.isoformat() if m.recorded_at else None,
        "latency_ms": round(m.latency_ms, 1) if m.latency_ms else 0.0,
        "status_code": m.status_code,
        "response_size_bytes": m.response_size_bytes,
        "is_error": m.is_error,
        "health_score": round(m.health_score, 1) if m.health_score else 100.0,
        "latency_anomaly": m.latency_anomaly,
        "error_spike": m.error_spike,
        "size_anomaly": m.size_anomaly,
        "lstm_anomaly": m.lstm_anomaly,
        "anomaly_reasons": m.anomaly_reasons,
    }


# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/admin/health", dependencies=[Depends(require_auth)])
async def get_all_health():
    """
    Returns a structured overview of platform health:
      - global: aggregated platform score, status, counts
      - endpoints: per-endpoint health snapshots (latest cache)
      - detector: adaptive detector stats (learned baselines per path)
      - lstm: LSTM neural network status and training info
      - ai_status: Transparency about which engines are currently active
    """
    global_health = health_monitor.get_global_health()
    endpoint_health = health_monitor.get_all_endpoint_health()

    # Enrich each endpoint with its detector stats (baseline info)
    enriched_endpoints = []
    for ep in endpoint_health:
        path = ep.get("path_pattern", "")
        stats = adaptive_detector.get_stats(path) if path else {}
        enriched_endpoints.append({
            **ep,
            "detector": {
                "mean_latency": stats.get("mean", 0.0),
                "std_latency": stats.get("std", 0.0),
                "samples": stats.get("count", 0),
                "mode": stats.get("mode", "learning"),
            } if stats.get("count", 0) > 0 else None,
        })

    # LSTM predictor stats & Training Countdown
    lstm_stats = {}
    training_progress = {"status": "unknown"}
    active_engines = ["Welford Statistical ML"]

    if lstm_predictor:
        lstm_stats = lstm_predictor.get_stats()
        
        # Calculate countdown from DB
        try:
            from ml.auto_retrain import MIN_TRAINING_OBSERVATIONS, NEW_DATA_THRESHOLD, IS_TRAINING
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    select(sa_func.count()).select_from(HealthMetric)
                    .where(HealthMetric.health_score >= 80.0)
                )
                total_normal = res.scalar() or 0
            
            if not lstm_predictor.is_active:
                remaining = max(0, MIN_TRAINING_OBSERVATIONS - total_normal)
                percent = min(100, round((total_normal / MIN_TRAINING_OBSERVATIONS) * 100))
                training_progress = {
                    "total_collected": total_normal,
                    "target_first_train": MIN_TRAINING_OBSERVATIONS,
                    "remaining": remaining,
                    "percent": percent,
                    "is_training": IS_TRAINING,
                    "status": "collecting_initial_baseline" if remaining > 0 else "training_ready"
                }
            else:
                active_engines.append("LSTM Neural Network")
                # Progress toward NEXT retrain
                last_trained_count = lstm_stats.get("training_sequences", 0)
                since_last = total_normal - last_trained_count
                training_progress = {
                    "total_collected": total_normal,
                    "since_last_train": since_last,
                    "next_retrain_threshold": NEW_DATA_THRESHOLD,
                    "percent": min(100, round((since_last / NEW_DATA_THRESHOLD) * 100)) if NEW_DATA_THRESHOLD > 0 else 100,
                    "is_training": IS_TRAINING,
                    "status": "improving_model"
                }
        except Exception as e:
            logger.warning(f"⚠️ Could not calculate training progress: {e}")

    return {
        "global": global_health,
        "endpoints": enriched_endpoints,
        "ai_status": {
            "active_engines": active_engines,
            "training_progress": training_progress,
            "is_hybrid": len(active_engines) > 1
        },
        "detector_summary": {
            "total_endpoints_tracked": len(adaptive_detector.endpoint_stats),
            "endpoints_in_learning": sum(
                1 for s in adaptive_detector.endpoint_stats.values()
                if s["count"] < 3
            ),
            "endpoints_active": sum(
                1 for s in adaptive_detector.endpoint_stats.values()
                if s["count"] >= 3
            ),
        },
        "lstm": lstm_stats,
    }


@router.get("/admin/health/global", dependencies=[Depends(require_auth)])
async def get_global_health():
    """
    Returns the aggregated platform health score and status.
    """
    return health_monitor.get_global_health()


@router.get("/admin/health/{endpoint_id}", dependencies=[Depends(require_auth)])
async def get_endpoint_health(
    endpoint_id: int,
    limit: int = Query(default=20, ge=1, le=100, description="Number of recent metrics to return"),
):
    """
    Returns structured health data for a specific endpoint:
      - current: latest cached health snapshot
      - history: recent metric records (configurable limit)
      - stats: computed summary (avg_latency, error_rate, anomaly_rate, status)
      - detector: adaptive anomaly detector baseline info
    """
    # Validate endpoint exists in the database
    async with AsyncSessionLocal() as session:
        ep_result = await session.execute(
            select(Endpoint).where(Endpoint.id == endpoint_id)
        )
        endpoint = ep_result.scalars().first()

        if not endpoint:
            raise HTTPException(
                status_code=404,
                detail=f"Endpoint with id {endpoint_id} not found."
            )

        # Get health monitor cache
        health = health_monitor.get_endpoint_health(endpoint_id)

        # Query historical metrics with configurable limit
        result = await session.execute(
            select(HealthMetric)
            .where(HealthMetric.endpoint_id == endpoint_id)
            .order_by(HealthMetric.recorded_at.desc())
            .limit(limit)
        )
        recent_metrics = result.scalars().all()

    # Compute summary statistics from history
    stats = _compute_history_stats(recent_metrics)

    # Get adaptive detector baseline for this endpoint's path
    path = endpoint.path_pattern
    detector_stats = adaptive_detector.get_stats(path) if path else {}

    return {
        "endpoint": {
            "id": endpoint.id,
            "method": endpoint.method,
            "path_pattern": endpoint.path_pattern,
        },
        "current": health,
        "stats": stats,
        "detector": detector_stats if detector_stats.get("count", 0) > 0 else {
            "count": 0,
            "mode": "learning",
            "message": "No latency baseline learned yet. Send traffic to this endpoint to begin.",
        },
        "history": [_format_metric(m) for m in recent_metrics],
    }


# ──────────────────────────────────────────────────────────────────────────────
# DETECTOR RESET ENDPOINTS
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/admin/detector/reset/{path:path}", dependencies=[Depends(require_auth)])
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
        known = list(adaptive_detector.endpoint_stats.keys())
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"No learned stats found for '{full_path}'.",
                "known_endpoints": known,
                "hint": "Use one of the known endpoint paths, or POST /admin/detector/reset-all to wipe everything.",
            }
        )

    # Capture stats before deletion for the response
    old_stats = adaptive_detector.get_stats(full_path)

    del adaptive_detector.endpoint_stats[full_path]

    # Safe flush — handle case where persist path doesn't exist
    try:
        adaptive_detector.flush()
    except Exception as e:
        logger.warning(f"⚠️ Could not persist detector state after reset: {e}")

    return {
        "status": "reset",
        "endpoint": full_path,
        "cleared_baseline": {
            "mean_ms": old_stats.get("mean", 0),
            "std_ms": old_stats.get("std", 0),
            "samples": old_stats.get("count", 0),
        },
        "message": f"Baseline for '{full_path}' cleared. It will re-learn from new traffic.",
    }


@router.post("/admin/detector/reset-all", dependencies=[Depends(require_auth)])
async def reset_all_stats():
    """
    Wipes ALL learned latency baselines across every endpoint.

    Use this when the platform was trained on contaminated traffic
    (e.g. during chaos testing or first-run anomalies).
    """
    count = len(adaptive_detector.endpoint_stats)
    cleared_paths = list(adaptive_detector.endpoint_stats.keys())

    adaptive_detector.endpoint_stats.clear()

    # Safe flush — handle case where persist path doesn't exist or disk error
    try:
        adaptive_detector.flush()
    except Exception as e:
        logger.warning(f"⚠️ Could not persist detector state after reset-all: {e}")

    return {
        "status": "reset",
        "endpoints_cleared": count,
        "cleared_paths": cleared_paths,
        "message": "All baselines wiped. The AI will re-learn from fresh traffic.",
    }


# ──────────────────────────────────────────────────────────────────────────────
# LSTM MODEL ENDPOINTS
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/admin/lstm/status", dependencies=[Depends(require_auth)])
async def get_lstm_status():
    """
    Returns the current status of the LSTM Anomaly Predictor:
      - Whether a model is loaded
      - Training statistics (when, how many sequences, etc.)
      - Prediction counts and anomaly rate
    """
    if lstm_predictor is None:
        return {
            "status": "disabled",
            "message": "PyTorch not installed. Install with: pip install torch",
        }

    return {
        "status": "active" if lstm_predictor.is_active else "untrained",
        **lstm_predictor.get_stats(),
    }


@router.post("/admin/lstm/train", dependencies=[Depends(require_auth)])
async def trigger_lstm_training():
    """
    Manually trigger LSTM Autoencoder training.

    This extracts normal traffic data from the health_metrics table,
    trains the model, and hot-swaps it into the running predictor.

    Use this when you've accumulated enough traffic and want to start
    ML-based anomaly detection immediately without waiting for the
    auto-retrain loop.
    """
    if lstm_predictor is None:
        raise HTTPException(
            status_code=503,
            detail="PyTorch not installed. Install with: pip install torch",
        )

    try:
        from ml.auto_retrain import _run_training

        logger.info("🧠 Manual LSTM training triggered...")
        result = await _run_training()

        if result is None:
            return {
                "status": "skipped",
                "message": (
                    "Not enough training data. Need at least 50 normal observations "
                    "per endpoint. Send more traffic through the proxy first."
                ),
            }

        if result.get("status") == "success":
            # Hot-swap the new model
            lstm_predictor.reload_model()

        return {
            "status": "success",
            **result,
        }

    except Exception as e:
        logger.error(f"❌ Manual LSTM training failed: {e}")
        raise HTTPException(status_code=500, detail=f"Training failed: {str(e)}")
