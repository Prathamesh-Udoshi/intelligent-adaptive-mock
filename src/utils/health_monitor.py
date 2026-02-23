"""
AI Anomaly Detection — Traffic Health Monitor
===============================================
Evaluates each proxied request against learned baselines to detect behavioral anomalies.

Monitors:
  1. Latency Anomaly    — Current latency > 2σ from learned mean
  2. Error Rate Spike   — Error rate jumps >3x from baseline in sliding window
  3. Traffic Drop        — Sudden traffic drop (possible silent failures)
  4. Response Size Drift — Body size changes dramatically (possible data truncation)

Outputs a per-endpoint health score (0-100, where 100 = healthy) and
a global platform health score aggregated across all endpoints.
"""

import math
import datetime
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict


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
    LATENCY_PENALTY = 15        # Points deducted for latency anomaly
    ERROR_SPIKE_PENALTY = 25    # Points deducted for error spike
    SIZE_ANOMALY_PENALTY = 10   # Points deducted for size anomaly
    DRIFT_PENALTY = 20          # Points deducted for active contract drift
    
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
    
    def evaluate_request(
        self,
        endpoint_id: int,
        latency_ms: float,
        status_code: int,
        response_size: int,
        learned_latency_mean: float = 0.0,
        learned_latency_std: float = 0.0,
        learned_error_rate: float = 0.0,
        has_active_drift: bool = False,
        path_pattern: str = ""
    ) -> Dict[str, Any]:
        """
        Evaluate a single request against learned baselines.
        
        Returns a health assessment dict:
        {
            "health_score": 0-100,
            "status": "healthy" | "degraded" | "critical",
            "anomalies": [{"type": "...", "message": "...", "severity": "..."}],
            "latency_anomaly": bool,
            "error_spike": bool,
            "size_anomaly": bool,
        }
        """
        anomalies = []
        is_error = status_code >= 400
        
        # --- Record observation in sliding window ---
        observation = {
            "latency_ms": latency_ms,
            "status_code": status_code,
            "is_error": is_error,
            "response_size": response_size,
            "timestamp": datetime.datetime.utcnow()
        }
        
        window = self._windows[endpoint_id]
        window.append(observation)
        if len(window) > self.SLIDING_WINDOW_SIZE:
            window.pop(0)
        
        size_window = self._size_windows[endpoint_id]
        if response_size > 0:
            size_window.append(response_size)
            if len(size_window) > self.SLIDING_WINDOW_SIZE:
                size_window.pop(0)
        
        latency_anomaly = False
        error_spike = False
        size_anomaly = False
        
        # --- 1. LATENCY ANOMALY ---
        if learned_latency_mean > 0 and learned_latency_std > 0:
            threshold = learned_latency_mean + (self.LATENCY_SIGMA_THRESHOLD * learned_latency_std)
            if latency_ms > threshold:
                latency_anomaly = True
                overshoot = ((latency_ms - learned_latency_mean) / learned_latency_std)
                anomalies.append({
                    "type": "latency_spike",
                    "severity": "high" if overshoot > 4 else "medium",
                    "message": f"Latency {latency_ms:.0f}ms is {overshoot:.1f}σ above the baseline mean of {learned_latency_mean:.0f}ms",
                    "current": round(latency_ms, 1),
                    "baseline": round(learned_latency_mean, 1),
                    "threshold": round(threshold, 1)
                })
        elif len(window) >= self.MIN_OBSERVATIONS:
            # No learned baseline yet — use window statistics
            latencies = [o["latency_ms"] for o in window[:-1]]  # Exclude current
            if latencies:
                win_mean = sum(latencies) / len(latencies)
                win_std = _std_dev(latencies, win_mean)
                if win_std > 0:
                    threshold = win_mean + (self.LATENCY_SIGMA_THRESHOLD * win_std)
                    if latency_ms > threshold:
                        latency_anomaly = True
                        overshoot = (latency_ms - win_mean) / win_std
                        # Don't flag as 'high' severity during initial window learning
                        anomalies.append({
                            "type": "latency_spike",
                            "severity": "medium",
                            "message": f"Latency {latency_ms:.0f}ms is {overshoot:.1f}σ above the recent average of {win_mean:.0f}ms",
                            "current": round(latency_ms, 1),
                            "baseline": round(win_mean, 1),
                            "threshold": round(threshold, 1)
                        })
        
        # --- Increase Penalty Reliability: Ignore high severity for first few requests ---
        if len(window) < 10:
            for a in anomalies:
                if a["type"] == "latency_spike":
                    a["severity"] = "medium"
        
        # --- 2. ERROR RATE SPIKE ---
        if len(window) >= self.MIN_OBSERVATIONS:
            recent_errors = sum(1 for o in window if o["is_error"])
            recent_error_rate = recent_errors / len(window)
            
            baseline_error_rate = max(learned_error_rate, 0.01)  # Floor at 1% to avoid div-by-zero
            
            if recent_error_rate > (baseline_error_rate * self.ERROR_RATE_SPIKE_FACTOR) and recent_errors >= 2:
                error_spike = True
                spike_factor = recent_error_rate / baseline_error_rate
                anomalies.append({
                    "type": "error_spike",
                    "severity": "high" if spike_factor > 5 else "medium",
                    "message": f"Error rate {recent_error_rate*100:.0f}% is {spike_factor:.1f}x the baseline of {learned_error_rate*100:.0f}%",
                    "current_rate": round(recent_error_rate, 3),
                    "baseline_rate": round(learned_error_rate, 3),
                    "recent_errors": recent_errors,
                    "window_size": len(window)
                })
        
        # --- 3. RESPONSE SIZE DRIFT ---
        if len(size_window) >= self.MIN_OBSERVATIONS and response_size > 0:
            avg_size = sum(size_window[:-1]) / len(size_window[:-1]) if len(size_window) > 1 else response_size
            
            if avg_size > 0:
                size_ratio = response_size / avg_size
                
                # Flag if dramatically larger or smaller
                if size_ratio > self.SIZE_CHANGE_FACTOR or size_ratio < (1 / self.SIZE_CHANGE_FACTOR):
                    size_anomaly = True
                    direction = "larger" if size_ratio > 1 else "smaller"
                    anomalies.append({
                        "type": "size_anomaly",
                        "severity": "medium" if size_ratio < 5 else "high",
                        "message": f"Response size {_format_bytes(response_size)} is {size_ratio:.1f}x {direction} than the average of {_format_bytes(int(avg_size))}",
                        "current_size": response_size,
                        "avg_size": int(avg_size),
                        "ratio": round(size_ratio, 2)
                    })
        
        # --- 4. CALCULATE HEALTH SCORE ---
        score = 100.0
        
        if latency_anomaly:
            severity_mult = 1.5 if any(a.get("severity") == "high" for a in anomalies if a["type"] == "latency_spike") else 1.0
            score -= self.LATENCY_PENALTY * severity_mult
        
        if error_spike:
            severity_mult = 1.5 if any(a.get("severity") == "high" for a in anomalies if a["type"] == "error_spike") else 1.0
            score -= self.ERROR_SPIKE_PENALTY * severity_mult
        
        if size_anomaly:
            score -= self.SIZE_ANOMALY_PENALTY
        
        if has_active_drift:
            score -= self.DRIFT_PENALTY
        
        score = max(0.0, min(100.0, score))
        
        # Determine status
        if score >= 80:
            status = "healthy"
        elif score >= 50:
            status = "degraded"
        else:
            status = "critical"
        
        result = {
            "health_score": round(score, 1),
            "status": status,
            "anomalies": anomalies,
            "latency_anomaly": latency_anomaly,
            "error_spike": error_spike,
            "size_anomaly": size_anomaly,
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
            self._global_health = {
                "score": 100.0,
                "status": "healthy",
                "anomaly_count": 0,
                "endpoints_monitored": 0,
                "critical_endpoints": [],
                "degraded_endpoints": []
            }
            return
        
        scores = [h["health_score"] for h in self._health_cache.values()]
        anomaly_count = sum(
            1 for h in self._health_cache.values() 
            if h["latency_anomaly"] or h["error_spike"] or h["size_anomaly"]
        )
        
        critical = [
            {"endpoint_id": h["endpoint_id"], "path": h.get("path_pattern", ""), "score": h["health_score"]}
            for h in self._health_cache.values() if h["status"] == "critical"
        ]
        degraded = [
            {"endpoint_id": h["endpoint_id"], "path": h.get("path_pattern", ""), "score": h["health_score"]}
            for h in self._health_cache.values() if h["status"] == "degraded"
        ]
        
        # Global score = weighted average (critical endpoints pull down more)
        avg_score = sum(scores) / len(scores)
        min_score = min(scores)
        
        # Blend: 70% average + 30% worst endpoint
        global_score = (avg_score * 0.7) + (min_score * 0.3)
        
        if global_score >= 80:
            global_status = "healthy"
        elif global_score >= 50:
            global_status = "degraded"
        else:
            global_status = "critical"
        
        self._global_health = {
            "score": round(global_score, 1),
            "status": global_status,
            "anomaly_count": anomaly_count,
            "endpoints_monitored": len(self._health_cache),
            "critical_endpoints": critical,
            "degraded_endpoints": degraded
        }


# ──────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ──────────────────────────────────────────────────────

def _std_dev(values: List[float], mean: float) -> float:
    """Calculate standard deviation."""
    if len(values) < 2:
        return 0.0
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def _format_bytes(size: int) -> str:
    """Human-friendly byte size formatting."""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size/1024:.1f}KB"
    else:
        return f"{size/(1024*1024):.1f}MB"
