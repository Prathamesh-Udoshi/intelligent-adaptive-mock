"""
Learning Service
=================
Core logic for processing the learning buffer, storing drift alerts,
health metrics, and managing the request log.
"""

import datetime
import logging
from typing import List, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AsyncSessionLocal
from core.state import (
    LEARNING_BUFFER, LEARNING_BUFFER_SIZE, RECENT_LOGS,
    buffer_lock, logs_lock, health_monitor
)
from core.websocket import manager
from core.models import Endpoint, EndpointBehavior, ChaosConfig, ContractDrift, HealthMetric
from utils.schema_learner import learn_schema

logger = logging.getLogger("mock_platform")


# ── Endpoint Lookup / Creation ──

async def get_or_create_endpoint(session: AsyncSession, method: str, path_pattern: str):
    """Find an existing endpoint or create a new one with default behavior + chaos config."""
    from core.state import TARGET_URL

    result = await session.execute(
        select(Endpoint).where(Endpoint.method == method, Endpoint.path_pattern == path_pattern)
    )
    endpoint = result.scalars().first()

    if not endpoint:
        endpoint = Endpoint(method=method, path_pattern=path_pattern, target_url=TARGET_URL)
        session.add(endpoint)
        await session.flush()

        behavior = EndpointBehavior(endpoint_id=endpoint.id)
        chaos = ChaosConfig(endpoint_id=endpoint.id)
        session.add(behavior)
        session.add(chaos)
        await session.commit()

    return endpoint


# ── Logging ──

async def add_to_logs(method: str, path: str, status: int, latency: int, type: str, has_drift: bool = False, health_info: dict = None):
    """Append a log entry and broadcast to WebSocket clients."""
    async with logs_lock:
        log_entry = {
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "method": method,
            "path": path,
            "status": status,
            "latency": round(latency),
            "type": type,
            "has_drift": has_drift,
            "health": health_info.get("status", "healthy") if health_info else "healthy",
            "health_score": health_info.get("health_score", 100) if health_info else 100
        }
        RECENT_LOGS.insert(0, log_entry)
        if len(RECENT_LOGS) > 50:
            RECENT_LOGS.pop()

    # Broadcast to all dashboard clients
    broadcast_data = {"type": "update", "data": log_entry}
    if health_info and health_info.get("anomalies"):
        broadcast_data["health_alert"] = health_info
    broadcast_data["global_health"] = health_monitor.get_global_health()
    await manager.broadcast(broadcast_data)


# ── Background Tasks ──

async def store_drift_alert(endpoint_id: int, drift_score: float, drift_summary: str, drift_details: List[Dict]):
    """Stores or updates a contract drift alert in the database. Prevents duplicates."""
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(ContractDrift).where(
                    ContractDrift.endpoint_id == endpoint_id,
                    ContractDrift.is_resolved == False
                ).order_by(ContractDrift.detected_at.desc())
            )
            existing_alerts = res.scalars().all()

            if existing_alerts:
                existing = existing_alerts[0]
                existing.detected_at = datetime.datetime.utcnow()
                existing.drift_score = drift_score
                existing.drift_summary = drift_summary
                existing.drift_details = drift_details

                # AUTO-CLEANUP orphaned duplicates
                if len(existing_alerts) > 1:
                    for orphaned in existing_alerts[1:]:
                        orphaned.is_resolved = True
                        orphaned.resolved_at = datetime.datetime.utcnow()
                    logger.info(f"🧹 Cleaned up {len(existing_alerts)-1} orphaned alerts for endpoint {endpoint_id}")

                logger.info(f"🔄 Updated existing drift alert for endpoint {endpoint_id}")
            else:
                drift_alert = ContractDrift(
                    endpoint_id=endpoint_id,
                    drift_score=drift_score,
                    drift_summary=drift_summary,
                    drift_details=drift_details
                )
                session.add(drift_alert)
                logger.info(f"🚨 New drift alert stored for endpoint {endpoint_id}")

            await session.commit()
    except Exception as e:
        logger.error(f"❌ Failed to store/update drift alert: {str(e)}")


async def store_health_metric(endpoint_id: int, latency_ms: float, status_code: int, response_size: int, health_result: dict):
    """Background task: stores a health metric snapshot in the database."""
    try:
        async with AsyncSessionLocal() as session:
            metric = HealthMetric(
                endpoint_id=endpoint_id,
                latency_ms=latency_ms,
                status_code=status_code,
                response_size_bytes=response_size,
                is_error=status_code >= 400,
                latency_anomaly=health_result.get("latency_anomaly", False),
                error_spike=health_result.get("error_spike", False),
                size_anomaly=health_result.get("size_anomaly", False),
                health_score=health_result.get("health_score", 100.0),
                anomaly_reasons=[a["message"] for a in health_result.get("anomalies", [])]
            )
            session.add(metric)
            await session.commit()
    except Exception as e:
        logger.error(f"❌ Failed to store health metric: {str(e)}")


# ── Learning Buffer Processor ──

async def process_learning_buffer():
    """Process accumulated traffic observations into learned behaviors."""
    import core.state as state

    async with buffer_lock:
        if len(state.LEARNING_BUFFER) < LEARNING_BUFFER_SIZE:
            return
        batch = state.LEARNING_BUFFER[:]
        state.LEARNING_BUFFER = []

    async with AsyncSessionLocal() as session:
        for item in batch:
            try:
                method = item['method']
                path_pattern = item['path_pattern']
                status = item['status']
                latency = item['latency']
                resp_body = item['response_body']
                req_body = item['request_body']

                endpoint = await get_or_create_endpoint(session, method, path_pattern)
                behavior_res = await session.execute(
                    select(EndpointBehavior).where(EndpointBehavior.endpoint_id == endpoint.id)
                )
                behavior = behavior_res.scalars().first()

                # Update EMA for Latency
                alpha = 0.1
                behavior.latency_mean = (behavior.latency_mean * (1 - alpha)) + (latency * alpha)

                # Update status code distributions
                status_str = str(status)
                if not behavior.status_code_distribution:
                    behavior.status_code_distribution = {status_str: 1.0}
                else:
                    dist = dict(behavior.status_code_distribution)
                    for k in dist:
                        dist[k] *= (1 - alpha)
                    dist[status_str] = dist.get(status_str, 0) + alpha

                    total = sum(dist.values())
                    behavior.status_code_distribution = {k: v/total for k, v in dist.items()}

                # Update Error Rate
                is_error = 1.0 if status >= 500 else 0.0
                behavior.error_rate = (behavior.error_rate * (1 - alpha)) + (is_error * alpha)

                # Learn Schema
                if status < 300 and resp_body and isinstance(resp_body, (dict, list)):
                    behavior.response_schema = learn_schema(behavior.response_schema, resp_body)

                if req_body and isinstance(req_body, (dict, list)):
                    behavior.request_schema = learn_schema(behavior.request_schema, req_body)

                session.add(behavior)
            except Exception as e:
                logger.error(f"❌ Error learning from request: {str(e)}")
                continue

        try:
            await session.commit()
        except Exception as e:
            logger.error(f"❌ Error committing learning batch: {str(e)}")
