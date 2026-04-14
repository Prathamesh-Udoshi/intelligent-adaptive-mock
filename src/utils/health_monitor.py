"""
AI Anomaly Detection — Traffic Health Monitor
===============================================
Evaluates each proxied request against learned baselines to detect behavioral anomalies.

Latency anomaly detection is delegated to AdaptiveAnomalyDetector (Welford's algorithm).
This class retains the sliding window for error rate spikes and response size drift.

Monitors:
  1. Latency Anomaly    — Welford Z-score against per-endpoint self-learned baseline
  2. Error Rate Spike   — Error rate jumps >3x from baseline in sliding window
  4. Response Size Drift — Body size changes dramatically (possible data truncation)

Outputs a per-endpoint health score (0-100, where 100 = healthy) and
a global platform health score aggregated across all endpoints.
"""

import math
import datetime
import logging
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger("mock_platform")

class HealthMonitor:
    """
    In-memory anomaly detector that evaluates requests against learned baselines.
    
    Uses a sliding window of recent observations per endpoint to detect:
    - Latency spikes (>2σ from learned baseline)
    - Error rate spikes (>3x from learned baseline)
    - Response size anomalies (>3x change from baseline)
    - Traffic drops (no requests in expected interval)
    
    The monitor is stateless across restarts — it builds up context from
    the learned EndpointBehavior data and live traffic.
    """
    
    # Configuration thresholds
    LATENCY_SIGMA_THRESHOLD = 2.0       # Flag if latency > mean + 2σ
    ERROR_RATE_SPIKE_FACTOR = 3.0       # Flag if error rate > 3x baseline
    SIZE_CHANGE_FACTOR = 3.0            # Flag if response size changes 3x
    SLIDING_WINDOW_SIZE = 50            # Number of recent observations to track
    MIN_OBSERVATIONS = 5                # Minimum observations before anomaly detection
    
    # Penalty weights for health score calculation
    LATENCY_PENALTY = 30        
    ERROR_SPIKE_PENALTY = 30    
    SIZE_ANOMALY_PENALTY = 15   
    DRIFT_PENALTY = 25          
    LSTM_ANOMALY_PENALTY = 20   
    
    def __init__(self):
        # Sliding window: endpoint_id -> list of recent observations
        self._windows: Dict[int, List[Dict]] = defaultdict(list)
        # Rolling response sizes: endpoint_id -> list of recent sizes
        self._size_windows: Dict[int, List[int]] = defaultdict(list)
        # Per-endpoint health cache
        self._health_cache: Dict[int, Dict] = {}
        # Global health
        self._global_health: Dict = {
            "score": 100.0,
            "status": "healthy",
            "anomaly_count": 0,
            "endpoints_monitored": 0
        }
    
    async def evaluate_request(
        self, 
        endpoint_id: int, 
        latency_ms: float, 
        status_code: int, 
        response_size: int,
        path_pattern: str = "",
        learned_error_rate: float = 0.05,
        has_active_drift: bool = False,
        detector: Optional[Any] = None,
        lstm_prediction: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Evaluate a single request against learned statistical and neural baselines.
        Returns a rich health snapshot for this endpoint.
        """
        if endpoint_id not in self._windows:
            self._windows[endpoint_id] = []
            self._size_windows[endpoint_id] = []
        
        window = self._windows[endpoint_id]
        size_window = self._size_windows[endpoint_id]
        
        # Append latest observation
        window.append({"latency_ms": latency_ms, "is_error": status_code >= 400})
        size_window.append(response_size)
        
        if len(window) > self.SLIDING_WINDOW_SIZE:
            window.pop(0)
        if len(size_window) > self.SLIDING_WINDOW_SIZE:
            size_window.pop(0)
        
        latency_anomaly = False
        error_spike = False
        size_anomaly = False
        lstm_anomaly = lstm_prediction.get("is_anomaly", False) if lstm_prediction else False
        
        anomalies = []

        # --- 1. LATENCY ANOMALY (Adaptive Detector) ---
        latency_stats = {"mean": 100, "std": 50} 
        if detector is not None and path_pattern:
            latency_detail = detector.get_anomaly_detail(path_pattern, latency_ms)
            latency_anomaly = latency_detail["is_anomaly"]
            latency_stats["mean"] = latency_detail["mean"]
            latency_stats["std"] = latency_detail["std"]
            if latency_anomaly:
                dyn_thresh = latency_detail.get("dynamic_threshold", 3.0)
                severity_icon = "high" if latency_detail["z_score"] > (dyn_thresh * 1.5) else "medium"
                anomalies.append({
                    "type": "latency_spike",
                    "severity": severity_icon,
                    "message": latency_detail["message"],
                })

        # --- 2. ERROR SPIKE ---
        recent_errors = sum(1 for o in window if o["is_error"])
        recent_error_rate = recent_errors / len(window)
        if len(window) >= 10 and recent_error_rate > (learned_error_rate * 3) and recent_error_rate > 0.2:
            error_spike = True
            anomalies.append({
                "type": "error_spike",
                "severity": "high",
                "message": f"Error rate spiked to {recent_error_rate:.0%} (Baseline: {learned_error_rate:.0%})"
            })

        # --- 3. SIZE ANOMALY ---
        if len(size_window) >= self.MIN_OBSERVATIONS:
            avg_size = sum(size_window[:-1]) / (len(size_window) - 1)
            latency_stats["avg_size"] = avg_size
            if response_size > (avg_size * 5) and response_size > 5000:
                size_anomaly = True
                anomalies.append({
                    "type": "size_outlier",
                    "severity": "medium",
                    "message": f"Response size {response_size/1024:.1f}KB is unusually large (Avg: {avg_size/1024:.1f}KB)"
                })

        # --- 4. LSTM NEURAL ANOMALY ---
        if lstm_anomaly:
            anomalies.append({
                "type": "lstm_pattern_anomaly",
                "severity": "high",
                "message": lstm_prediction.get("message", "Neural network detected unusual traffic pattern")
            })

        # ── 5. Build Final Score ──
        score = 100.0
        if latency_anomaly: score -= self.LATENCY_PENALTY
        if error_spike:     score -= self.ERROR_SPIKE_PENALTY
        if size_anomaly:    score -= self.SIZE_ANOMALY_PENALTY
        if lstm_anomaly:    score -= self.LSTM_ANOMALY_PENALTY
        if has_active_drift: score -= self.DRIFT_PENALTY
        
        score = max(0.0, score)
        
        status = "healthy"
        if score > 80:     status = "healthy"
        elif score > 50:   status = "degraded"
        else:              status = "critical"
        
        # ── 6. Natural Language Narrative (Neuro-Analytic AI) ──
        human_narrative = ""
        if anomalies:
            try:
                from utils.health_narrator import generate_health_narrative
                human_narrative = await generate_health_narrative(
                    path_pattern, method="REQ", 
                    latency=latency_ms, status=status_code, size=response_size,
                    baselines=latency_stats,
                    lstm_anomaly=lstm_anomaly
                )
            except Exception as e:
                logger.warning(f"Narrator failed: {e}")

        result = {
            "health_score": round(score, 1),
            "status": status,
            "anomalies": anomalies,
            "human_narrative": human_narrative,
            "latency_anomaly": latency_anomaly,
            "error_spike": error_spike,
            "size_anomaly": size_anomaly,
            "lstm_anomaly": lstm_anomaly,
            "has_drift": has_active_drift,
            "observations": len(window),
            "endpoint_id": endpoint_id,
            "path_pattern": path_pattern
        }
        
        # Update cache
        self._health_cache[endpoint_id] = result
        self._update_global_health()
        
        return result
    
    def get_endpoint_health(self, endpoint_id: int) -> Dict[str, Any]:
        """Get the latest cached health for a specific endpoint."""
        return self._health_cache.get(endpoint_id, {
            "health_score": 100.0,
            "status": "healthy",
            "anomalies": [],
            "human_narrative": "",
            "latency_anomaly": False,
            "error_spike": False,
            "size_anomaly": False,
            "has_drift": False,
            "observations": 0,
            "endpoint_id": endpoint_id,
            "path_pattern": ""
        })
    
    def get_global_health(self) -> Dict[str, Any]:
        """Get the aggregated platform health status."""
        return self._global_health.copy()
    
    def get_all_endpoint_health(self) -> List[Dict[str, Any]]:
        """Get health data for all monitored endpoints."""
        return list(self._health_cache.values())
    
    def _update_global_health(self):
        """Recalculate global platform health from all endpoint health caches."""
        if not self._health_cache:
            return
        
        scores = [h["health_score"] for h in self._health_cache.values()]
        anomaly_count = sum(
            1 for h in self._health_cache.values() 
            if h["latency_anomaly"] or h["error_spike"] or h["size_anomaly"] or h.get("lstm_anomaly", False)
        )
        
        avg_score = sum(scores) / len(scores)
        min_score = min(scores)
        global_score = (avg_score * 0.7) + (min_score * 0.3)
        
        if global_score >= 80:     global_status = "healthy"
        elif global_score >= 50:   global_status = "degraded"
        else:                      global_status = "critical"
        
        # Dynamic sensitivity tracking
        from core.state import adaptive_detector
        sensitivities = []
        for h in self._health_cache.values():
            stats = adaptive_detector.get_stats(h.get("path_pattern", ""))
            thresh = adaptive_detector._get_dynamic_threshold(stats.get("mean", 0), stats.get("std", 0)) if hasattr(adaptive_detector, '_get_dynamic_threshold') else 3.0
            sensitivities.append(thresh)
        
        avg_sensitivity = sum(sensitivities) / len(sensitivities) if sensitivities else 3.0

        self._global_health = {
            "score": round(global_score, 1),
            "status": global_status,
            "anomaly_count": anomaly_count,
            "endpoints_monitored": len(self._health_cache),
            "avg_sensitivity": round(avg_sensitivity, 1)
        }

def _std_dev(values: List[float], mean: float) -> float:
    if len(values) < 2: return 0.0
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)

def _format_bytes(size: int) -> str:
    if size < 1024: return f"{size}B"
    elif size < 1024 * 1024: return f"{size/1024:.1f}KB"
    else: return f"{size/(1024*1024):.1f}MB"
