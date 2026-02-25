"""
Proxy Service
==============
The catch-all proxy handler and mock response generator.
This is the heart of the platform: proxy → learn → detect drift → monitor health → fallback to mock.
"""

import time
import random
import asyncio
import logging

import httpx
import numpy as np
from fastapi import APIRouter, Request, Response, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from core.database import AsyncSessionLocal
import core.state as state
from core.state import (
    PLATFORM_STATE, CHAOS_PROFILES, LEARNING_BUFFER,
    buffer_lock, health_monitor, adaptive_detector
)
from core.models import EndpointBehavior, ChaosConfig, ContractDrift
from services.learning import (
    get_or_create_endpoint, add_to_logs, store_drift_alert,
    store_health_metric, process_learning_buffer
)
from utils.normalization import normalize_path
from utils.schema_learner import generate_mock_response
from utils.schema_intelligence import learn_and_compare, contract_reporter

logger = logging.getLogger("mock_platform")

router = APIRouter()


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def catch_all(request: Request, path: str, background_tasks: BackgroundTasks):
    method = request.method
    normalized = normalize_path(f"/{path}")

    # ── Guard 1: Never proxy or learn /admin/* paths ──────────────────────────
    # Admin routes that don't match a named handler (e.g. GET to a POST-only
    # admin endpoint) must 404 cleanly — not fall through to the proxy and get
    # stored as learned endpoints.
    if normalized.startswith("/admin/"):
        raise HTTPException(status_code=404, detail="Not found")

    # ── Guard 2: Refuse to proxy when no target is configured ─────────────────
    # If TARGET_URL is empty the platform is not set up.  Random browser
    # navigation would otherwise create garbage endpoint rows in the database.
    if not state.TARGET_URL:
        raise HTTPException(
            status_code=503,
            detail="No target URL configured. Sign in to the console and set one.",
        )

    # Mode Selection: Header > Global State
    mock_header = request.headers.get("X-Mock-Enabled")
    if mock_header:
        mock_enabled = mock_header.lower() == "true"
    else:
        mock_enabled = PLATFORM_STATE["mode"] == "mock"

    if method == "OPTIONS":
        return Response(status_code=200)

    async with AsyncSessionLocal() as session:
        endpoint = await get_or_create_endpoint(session, method, normalized)
        await session.commit()  # Persist new endpoint if just created
        behavior_res = await session.execute(
            select(EndpointBehavior).where(EndpointBehavior.endpoint_id == endpoint.id)
        )
        behavior = behavior_res.scalars().first()

        chaos_res = await session.execute(
            select(ChaosConfig).where(ChaosConfig.endpoint_id == endpoint.id)
        )
        chaos = chaos_res.scalars().first()

    # 1. MOCK MODE (Explicit)
    if mock_enabled:
        return await generate_endpoint_mock(behavior, chaos, normalized, request)


    # 2. PROXY MODE (With Automatic Mock Fallback)
    target_full_url = f"{state.TARGET_URL}/{path}"
    start_time = time.time()

    # Pre-read request body for learning
    req_body_bytes = await request.body()
    try:
        req_body_json = await request.json() if req_body_bytes else None
    except Exception:
        req_body_json = None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
            proxy_resp = await client.request(
                method=method,
                url=target_full_url,
                headers=headers,
                params=dict(request.query_params),
                content=req_body_bytes,
                follow_redirects=False
            )

        latency_ms = (time.time() - start_time) * 1000

        # Try to parse response JSON for learning
        try:
            resp_body_json = proxy_resp.json()
        except Exception:
            resp_body_json = None
            logger.debug(f"ℹ️ Response body for {normalized} is not valid JSON. Skipping schema learning.")

        if PLATFORM_STATE["learning_enabled"]:
            async with buffer_lock:
                LEARNING_BUFFER.append({
                    "method": method, "path_pattern": normalized,
                    "status": proxy_resp.status_code if proxy_resp else 502,
                    "latency": latency_ms,
                    "response_body": resp_body_json,
                    "request_body": req_body_json
                })
                background_tasks.add_task(process_learning_buffer)

        # CONTRACT DRIFT DETECTION (Schema Intelligence Engine)
        has_drift_detected = False
        if resp_body_json and isinstance(resp_body_json, (dict, list)):
            _, changes = learn_and_compare(normalized, resp_body_json, req_body_json)

            # Only flag BREAKING or WARNING changes as "drift" worth alerting on
            severe_changes = [c for c in changes if c["severity"] in ("BREAKING", "WARNING")]
            if severe_changes:
                has_drift_detected = True
                report = contract_reporter.generate(
                    # Re-build ContractChange objects from dicts for the reporter
                    # (generate() also accepts pre-built dicts via the change_dicts path)
                    [],   # Empty — narrative already logged by learn_and_compare
                    endpoint=normalized
                )
                drift_score  = min(100.0, len([c for c in changes if c["severity"] == "BREAKING"]) * 10.0
                                        + len([c for c in changes if c["severity"] == "WARNING"]) * 5.0)
                drift_summary = f"{len(severe_changes)} contract change(s): " + \
                    ", ".join(f"{c['change_type']} at {c['path']}" for c in severe_changes[:3])

                background_tasks.add_task(
                    store_drift_alert,
                    endpoint.id,
                    drift_score,
                    drift_summary,
                    severe_changes
                )

        # HEALTH MONITORING (Adaptive Anomaly Detection)
        response_size = len(proxy_resp.content) if proxy_resp.content else 0

        # Feed this latency into the Welford detector — updates per-endpoint baseline
        adaptive_detector.update(normalized, latency_ms)

        has_active_drift_for_health = has_drift_detected
        if not has_active_drift_for_health and behavior:
            async with AsyncSessionLocal() as health_session:
                drift_check = await health_session.execute(
                    select(ContractDrift)
                    .where(ContractDrift.endpoint_id == endpoint.id, ContractDrift.is_resolved == False)
                    .limit(1)
                )
                has_active_drift_for_health = drift_check.scalars().first() is not None

        health_result = health_monitor.evaluate_request(
            endpoint_id=endpoint.id,
            latency_ms=latency_ms,
            status_code=proxy_resp.status_code,
            response_size=response_size,
            path_pattern=normalized,
            learned_error_rate=behavior.error_rate if behavior else 0,
            has_active_drift=has_active_drift_for_health,
            detector=adaptive_detector,         # ← Welford-based latency detector
        )

        # Log anomalies to console
        if health_result["anomalies"]:
            for anomaly in health_result["anomalies"]:
                severity_icon = "🔴" if anomaly["severity"] == "high" else "🟡"
                logger.warning(f"{severity_icon} HEALTH ANOMALY [{normalized}]: {anomaly['message']}")

        # Store health metric in background
        background_tasks.add_task(
            store_health_metric,
            endpoint.id,
            latency_ms,
            proxy_resp.status_code,
            response_size,
            health_result
        )

        await add_to_logs(method, normalized, proxy_resp.status_code, latency_ms, "Proxy", has_drift=has_drift_detected, health_info=health_result)

        return Response(
            content=proxy_resp.content,
            status_code=proxy_resp.status_code,
            headers=dict(proxy_resp.headers)
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
        latency_ms = (time.time() - start_time) * 1000
        # AUTOMATIC FAILOVER: Backend is down, serve a mock instead!
        logger.warning(f"⚠️ PROXY FAILOVER: Backend {state.TARGET_URL} unreachable. Error: {str(e)}")
        
        # RECORD THIS FAIL OVER AS AN OBSERVATION
        if PLATFORM_STATE["learning_enabled"]:
            async with buffer_lock:
                LEARNING_BUFFER.append({
                    "method": method, "path_pattern": normalized,
                    "status": 502, "latency": latency_ms,
                    "response_body": None, "request_body": req_body_json
                })
        
        return await generate_endpoint_mock(behavior, chaos, normalized, request, is_failover=True)
    except Exception as e:
        logger.error(f"💥 UNEXPECTED PROXY ERROR: {str(e)}")
        raise HTTPException(status_code=502, detail=f"Proxy Error: {str(e)}")


async def generate_endpoint_mock(behavior, chaos, normalized, request, is_failover=False):
    """Generate a mock response using learned behavior patterns and chaos configuration."""
    try:
        # Load Active Profile
        profile_key = PLATFORM_STATE.get("active_chaos_profile", "normal")
        profile = CHAOS_PROFILES.get(profile_key, CHAOS_PROFILES["normal"])

        # Apply Chaos Level
        effective_chaos = chaos.chaos_level if chaos and chaos.is_active else 0
        if profile.get("global_chaos", 0) > 0:
            effective_chaos = max(effective_chaos, profile["global_chaos"])

        header_chaos = request.headers.get("X-Chaos-Level")
        if header_chaos:
            try:
                effective_chaos = int(header_chaos)
            except: pass

        # Decide Error vs Success
        error_prob = behavior.error_rate if behavior else 0
        error_prob += (effective_chaos / 100.0)

        if random.random() < min(error_prob, 0.9):
            log_status = 500
            await add_to_logs(request.method, normalized, log_status, 0, "Mock")
            return JSONResponse(
                content={"error": "Status Injected (AI/Chaos)", "endpoint": normalized, "failover": is_failover, "profile": profile["name"]},
                status_code=log_status
            )

        # Simulate Latency with realistic variance
        base_latency = behavior.latency_mean if behavior else 50
        latency_std = behavior.latency_std if behavior else 20

        # Profile Latency Boosts
        latency_boost = profile.get("latency_boost", 0)
        method_boosts = profile.get("latency_boost_methods", {})
        if request.method in method_boosts:
            latency_boost = max(latency_boost, method_boosts[request.method])

        latency = max(10, np.random.normal(base_latency, latency_std)) + (effective_chaos * 10) + latency_boost
        await asyncio.sleep(latency / 1000.0)

        # Choose Status Code
        status_code = 200
        if behavior and behavior.status_code_distribution:
            codes = list(behavior.status_code_distribution.keys())
            probs = list(behavior.status_code_distribution.values())
            try:
                status_code = int(random.choices(codes, weights=probs)[0])
            except:
                status_code = 200

        # Generate Body
        if profile.get("corrupt_responses"):
            mock_body = "xXx" * random.randint(5, 20) + "CORRUPTED_STREAM" + "xXx" * random.randint(5, 20)
            await add_to_logs(request.method, normalized, 200, latency, "Mock")
            return Response(content=mock_body, status_code=200, media_type="text/plain")

        try:
            req_body = await request.json()
        except:
            req_body = {}

        schema = behavior.response_schema if behavior else None
        mock_body = generate_mock_response(schema, req_body)

        if not mock_body:
            mock_body = {"message": "AI fallback (No patterns learned yet)", "endpoint": normalized}

        if isinstance(mock_body, dict) and is_failover:
            mock_body["_meta"] = "Generated via AI Fallback (Backend Unreachable)"

        await add_to_logs(request.method, normalized, status_code, latency, "Mock")

        return JSONResponse(content=mock_body, status_code=status_code)
    except Exception as e:
        logger.error(f"❌ MOCK GENERATION FAILED: {str(e)}")
        return JSONResponse(
            content={"error": "Mock Generation Failed", "detail": str(e)},
            status_code=500
        )
