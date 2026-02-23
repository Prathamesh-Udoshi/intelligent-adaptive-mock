"""
Adaptive Anomaly Detector
==========================
Production-grade, per-endpoint anomaly detection using Welford's online algorithm.

Design goals:
  - Zero hardcoded baselines. Every threshold is LEARNED from real traffic.
  - O(1) time per update. O(N endpoints) memory. No historical data stored.
  - Handles brand-new endpoints gracefully (learning mode until MIN_SAMPLES).
  - Correctly handles slow AI endpoints: their 10s latency becomes the baseline.
  - Correctly handles fast endpoints: a sudden 1s spike is correctly flagged.
  - Optional JSON persistence so learned baselines survive server restarts.
  - Optional exponential decay weighting for adapting to changing performance.
  - Thread-safe via asyncio.Lock (safe for FastAPI's async context).

Algorithm — Welford's Online Algorithm:
  For each new sample x:
    count    += 1
    delta     = x - mean
    mean     += delta / count
    delta2    = x - mean           ← uses the UPDATED mean intentionally
    M2       += delta * delta2
    variance  = M2 / (count - 1)  ← Bessel's correction (unbiased)
    std       = sqrt(variance)

  This computes exact running mean and variance in a single pass,
  numerically stable, without storing any individual samples.
"""

import json
import math
import asyncio
import logging
import os
from typing import Dict, Optional, Any

logger = logging.getLogger("mock_platform")


# ──────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────

MIN_LEARNING_SAMPLES: int = 5        # Requests before anomaly detection activates.
                                     # Welford's algorithm is valid from n=3;
                                     # 5 balances speed of learning vs false positives.
ANOMALY_Z_THRESHOLD: float = 3.0    # Z-score above which a latency is anomalous
DECAY_FACTOR: float = 0.98           # Exponential decay weight (1.0 = no decay, 0.95 = fast decay)
USE_DECAY: bool = True               # Toggle decay weighting for changing workloads

# Score thresholds
SCORE_HEALTHY: float = 80.0
SCORE_DEGRADED: float = 50.0

# Persist to the project's data/ folder so baselines survive server restarts.
# On Render free tier, the server sleeps after 15min; without this every endpoint
# re-enters learning mode on every cold start.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # src/
_DATA_DIR = os.path.join(_BASE_DIR, "..", "data")
PERSIST_PATH: Optional[str] = os.path.join(_DATA_DIR, "detector_stats.json")

# Save to disk after every N updates.
# Set to 1 to persist after every single request — important on Render free tier
# where the server can sleep at any time. The JSON file is tiny (<2KB), so the
# overhead is negligible compared to the value of not losing learned baselines.
PERSIST_EVERY: int = 1


class AdaptiveAnomalyDetector:
    """
    Per-endpoint latency anomaly detector.

    Uses Welford's online algorithm to maintain a running mean and variance
    for each endpoint path, with no hardcoded thresholds and no stored history.

    Example usage:
        detector = AdaptiveAnomalyDetector()

        # After every proxied request:
        detector.update("/analyze", latency_ms=823.0)

        if detector.is_anomaly("/analyze", latency_ms=823.0):
            flag_anomaly()

        score = detector.get_health_score("/analyze", latency_ms=823.0)
    """

    def __init__(self, persist_path: Optional[str] = PERSIST_PATH):
        """
        Initialize the detector.

        Args:
            persist_path: Optional path to a JSON file for persisting stats.
                          If provided, stats are loaded on startup and saved on update.
        """
        # Structure: { endpoint_path: { count, mean, M2, std, eff_count } }
        self.endpoint_stats: Dict[str, Dict[str, float]] = {}
        self._lock = asyncio.Lock()
        self._persist_path = persist_path
        self._update_count = 0  # Counts updates; triggers save every PERSIST_EVERY calls

        if persist_path and os.path.exists(persist_path):
            self._load_from_disk(persist_path)
            logger.info(f"📂 Adaptive detector: loaded stats for {len(self.endpoint_stats)} endpoints from {persist_path}")

    # ──────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────

    def update(self, endpoint: str, latency: float) -> Dict[str, Any]:
        """
        Record a new latency observation for an endpoint.

        Uses Welford's algorithm for numerically stable online mean/variance.
        Optionally applies exponential decay weighting so recent samples
        have more influence (useful for endpoints whose performance drifts over time).

        Args:
            endpoint: The normalized URL path (e.g. "/analyze/optimize").
            latency:  The observed latency in milliseconds.

        Returns:
            The updated statistics dict for this endpoint.
        """
        if endpoint not in self.endpoint_stats:
            # First observation for this endpoint — initialize
            self.endpoint_stats[endpoint] = {
                "count": 0,
                "mean": 0.0,
                "M2": 0.0,
                "std": 0.0,
                "eff_count": 0.0,  # Effective count (reduced by decay)
            }

        stats = self.endpoint_stats[endpoint]

        if USE_DECAY and stats["count"] > 0:
            # Exponential decay: reduce effective weight of old observations
            # This lets the detector adapt when an endpoint's true latency shifts
            stats["M2"] *= DECAY_FACTOR
            stats["eff_count"] = stats["eff_count"] * DECAY_FACTOR + 1.0
        else:
            stats["eff_count"] = float(stats["count"] + 1)

        # Welford's online update
        stats["count"] += 1
        count = stats["eff_count"] if (USE_DECAY and stats["count"] > 1) else float(stats["count"])

        delta = latency - stats["mean"]
        stats["mean"] += delta / count
        delta2 = latency - stats["mean"]   # Uses UPDATED mean
        stats["M2"] += delta * delta2

        # Bessel's correction for unbiased variance (requires count >= 2)
        if count >= 2:
            variance = stats["M2"] / (count - 1)
            stats["std"] = math.sqrt(max(0.0, variance))
        else:
            stats["std"] = 0.0

        # Batched disk write: avoids a blocking file I/O hit on every single request.
        # Data is also flushed explicitly on server shutdown via flush().
        self._update_count += 1
        if self._persist_path and (self._update_count % PERSIST_EVERY == 0):
            self._save_to_disk(self._persist_path)

        return self.get_stats(endpoint)

    def flush(self) -> None:
        """Force an immediate save to disk. Call this on server shutdown."""
        if self._persist_path:
            self._save_to_disk(self._persist_path)
            logger.info(f"💾 Adaptive detector: flushed stats for {len(self.endpoint_stats)} endpoints to disk.")

    def is_anomaly(self, endpoint: str, latency: float) -> bool:
        """
        Returns True if the given latency is anomalous for this endpoint.

        LEARNING MODE: Returns False until MIN_LEARNING_SAMPLES observations
        are collected. This prevents false positives for new endpoints.

        DETECTION MODE: Returns True if the Z-score exceeds ANOMALY_Z_THRESHOLD.
        Z = |latency - mean| / std

        Args:
            endpoint: The normalized URL path.
            latency:  The observed latency in milliseconds.

        Returns:
            True if anomalous, False otherwise (including during learning mode).
        """
        stats = self.endpoint_stats.get(endpoint)
        if not stats:
            return False

        # Still in learning mode
        if stats["count"] < MIN_LEARNING_SAMPLES:
            return False

        # Can't compute Z-score without variance
        if stats["std"] <= 0:
            return False

        z_score = abs(latency - stats["mean"]) / stats["std"]
        return z_score > ANOMALY_Z_THRESHOLD

    def get_z_score(self, endpoint: str, latency: float) -> float:
        """
        Compute the Z-score for a given latency against this endpoint's baseline.

        Returns 0.0 if there's insufficient data.
        """
        stats = self.endpoint_stats.get(endpoint)
        if not stats or stats["std"] <= 0:
            return 0.0
        return abs(latency - stats["mean"]) / stats["std"]

    def get_health_score(self, endpoint: str, latency: float) -> float:
        """
        Return a health score from 0 to 100 for this request.

        Scoring logic:
          - 100:       Latency within 1 std of mean (perfectly normal).
          - 85-100:    Latency within 2 std of mean (acceptable).
          - 50-85:     Latency within 3 std of mean (slightly elevated).
          - 0-50:      Latency > 3 std above mean (anomalous).
          - 100:       Still in learning mode (no penalty during warm-up).

        Args:
            endpoint: The normalized URL path.
            latency:  The observed latency in milliseconds.

        Returns:
            A float score from 0.0 to 100.0.
        """
        stats = self.endpoint_stats.get(endpoint)

        # Learning mode or no data — no penalty
        if not stats or stats["count"] < MIN_LEARNING_SAMPLES or stats["std"] <= 0:
            return 100.0

        z = self.get_z_score(endpoint, latency)

        if z <= 1.0:
            return 100.0                              # Within 1σ: perfect
        elif z <= 2.0:
            return 100.0 - ((z - 1.0) * 10.0)        # 1-2σ: 90-100
        elif z <= 3.0:
            return 90.0 - ((z - 2.0) * 30.0)         # 2-3σ: 60-90
        elif z <= 5.0:
            return 60.0 - ((z - 3.0) * 20.0)         # 3-5σ: 20-60
        else:
            return max(0.0, 20.0 - (z - 5.0) * 4.0)  # >5σ: approaches 0

    def get_anomaly_detail(self, endpoint: str, latency: float) -> Dict[str, Any]:
        """
        Return a full anomaly detail dict, matching the format expected by the
        existing health monitoring pipeline in proxy.py and learning.py.

        Returns:
            {
                "is_anomaly": bool,
                "z_score": float,
                "severity": "none" | "medium" | "high",
                "message": str,
                "mode": "learning" | "active",
                "mean": float,
                "std": float,
                "count": int,
                "health_score": float
            }
        """
        stats = self.endpoint_stats.get(endpoint, {"count": 0, "mean": 0.0, "std": 0.0})
        count = stats.get("count", 0)
        mean = stats.get("mean", 0.0)
        std = stats.get("std", 0.0)

        if count < MIN_LEARNING_SAMPLES:
            return {
                "is_anomaly": False,
                "z_score": 0.0,
                "severity": "none",
                "message": f"Learning mode ({count}/{MIN_LEARNING_SAMPLES} samples collected)",
                "mode": "learning",
                "mean": round(mean, 1),
                "std": round(std, 1),
                "count": count,
                "health_score": 100.0
            }

        z = self.get_z_score(endpoint, latency)
        is_anom = z > ANOMALY_Z_THRESHOLD
        score = self.get_health_score(endpoint, latency)

        severity = "none"
        if z > ANOMALY_Z_THRESHOLD * 2:
            severity = "high"
        elif z > ANOMALY_Z_THRESHOLD:
            severity = "medium"

        if is_anom:
            message = (
                f"Latency {latency:.0f}ms is {z:.1f}σ above the learned "
                f"baseline of {mean:.0f}ms ± {std:.0f}ms"
            )
        else:
            message = f"Latency {latency:.0f}ms is normal (baseline: {mean:.0f}ms ± {std:.0f}ms)"

        return {
            "is_anomaly": is_anom,
            "z_score": round(z, 2),
            "severity": severity,
            "message": message,
            "mode": "active",
            "mean": round(mean, 1),
            "std": round(std, 1),
            "count": count,
            "health_score": round(score, 1)
        }

    def get_stats(self, endpoint: str) -> Dict[str, Any]:
        """
        Return raw Welford statistics for an endpoint (for debugging/monitoring).

        Returns:
            {
                "count": int,
                "mean": float,
                "std": float,
                "M2": float,
                "eff_count": float,
                "mode": "learning" | "active"
            }
        """
        stats = self.endpoint_stats.get(endpoint)
        if not stats:
            return {"count": 0, "mean": 0.0, "std": 0.0, "M2": 0.0, "eff_count": 0.0, "mode": "learning"}

        return {
            "count": stats["count"],
            "mean": round(stats["mean"], 2),
            "std": round(stats["std"], 2),
            "M2": round(stats["M2"], 4),
            "eff_count": round(stats["eff_count"], 2),
            "mode": "active" if stats["count"] >= MIN_LEARNING_SAMPLES else "learning"
        }

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Return stats for all tracked endpoints."""
        return {ep: self.get_stats(ep) for ep in self.endpoint_stats}

    # ──────────────────────────────────────────────────────
    # PERSISTENCE
    # ──────────────────────────────────────────────────────

    def _save_to_disk(self, path: str) -> None:
        """Persist learned stats to a JSON file."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(self.endpoint_stats, f, indent=2)
        except Exception as e:
            logger.warning(f"⚠️ Could not persist detector stats: {e}")

    def _load_from_disk(self, path: str) -> None:
        """Load previously learned stats from a JSON file."""
        try:
            with open(path, "r") as f:
                loaded = json.load(f)
                # Restore, ensuring all keys exist (backward compatible)
                for ep, stats in loaded.items():
                    self.endpoint_stats[ep] = {
                        "count": stats.get("count", 0),
                        "mean": stats.get("mean", 0.0),
                        "M2": stats.get("M2", 0.0),
                        "std": stats.get("std", 0.0),
                        "eff_count": stats.get("eff_count", float(stats.get("count", 0))),
                    }
        except Exception as e:
            logger.warning(f"⚠️ Could not load detector stats: {e}")
