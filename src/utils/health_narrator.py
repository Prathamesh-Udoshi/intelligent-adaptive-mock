"""
Health Narrator
===============
Uses LLM to provide natural language diagnostics for API anomalies detected
by the statistical and neural engines.
"""

import json
import logging
import os
from typing import List, Optional

logger = logging.getLogger("mock_platform")

_SYSTEM_PROMPT = """\
You are an expert Reliability Engineer and Data Scientist. Your task is to analyze
API telemetry anomalies detected by a statistical engine (Welford) and a neural network (LSTM).
Provide a concise, 1-2 sentence "Human Diagnostic" that explains WHAT happened and WHY it's unusual.

Tone: Professional, clinical, and data-driven.
Style: One short paragraph. No pleasantries. No "I detect...". Start directly with the diagnostic.
Example: "High latency spike of 1.2s detected. This exceeds the learned baseline of 150ms by 8x, while request size remained constant, suggesting a bottleneck in backend processing or database lock."
"""

async def generate_health_narrative(
    path: str,
    method: str,
    latency: float,
    status: int,
    size: int,
    baselines: dict,
    lstm_anomaly: bool = False
) -> str:
    """
    Generate a human-readable diagnostic report for an anomaly.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "Anomaly detected in traffic patterns."

    # Build a compact context for the LLM
    context = {
        "endpoint": f"{method} {path}",
        "current_metrics": {
            "latency_ms": round(latency, 1),
            "status_code": status,
            "response_size_bytes": size,
            "lstm_flag": lstm_anomaly
        },
        "learned_baselines": {
            "avg_latency": round(baselines.get("mean", 0), 1),
            "std_dev": round(baselines.get("std", 0), 1),
            "avg_size": round(baselines.get("avg_size", 0), 1)
        }
    }

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)

        # We use a very low temperature for consistent diagnostic reasoning
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Analyze this anomaly data: {json.dumps(context)}"}
            ],
            temperature=0.3,
            max_tokens=150
        )

        narrative = completion.choices[0].message.content.strip()
        logger.info(f"🧠 Generated health narrative for {method} {path}")
        return narrative

    except Exception as e:
        logger.warning(f"⚠️ Health narrative generation failed: {e}")
        return f"Telemetry spike: {latency}ms exceeds baseline {baselines.get('mean', 0)}ms."
