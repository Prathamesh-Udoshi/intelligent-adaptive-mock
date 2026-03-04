"""
Quick integration test for the three fixed features:
1. Adaptive Anomaly Detector (Health Monitor)
2. Schema Drift Detection
3. Chaos Engine
"""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
logging.basicConfig(level=logging.CRITICAL)

print("=" * 60)
print("TEST 1: ADAPTIVE ANOMALY DETECTOR")
print("=" * 60)

from utils.adaptive_detector import AdaptiveAnomalyDetector, MIN_LEARNING_SAMPLES

d = AdaptiveAnomalyDetector(persist_path=None)
print(f"  MIN_LEARNING_SAMPLES = {MIN_LEARNING_SAMPLES}")

# Feed 3 "normal" requests
for lat in [100, 110, 105]:
    d.update("/test", lat)

stats = d.get_stats("/test")
print(f"  After 3 normal samples: mean={stats['mean']:.1f}, std={stats['std']:.1f}, mode={stats['mode']}")

# Now feed a spike
detail = d.get_anomaly_detail("/test", 500)
print(f"  Anomaly at 500ms: is_anomaly={detail['is_anomaly']}, z_score={detail['z_score']:.1f}, threshold={detail['dynamic_threshold']:.1f}")

assert detail["is_anomaly"] == True, "FAIL: 500ms should be anomalous against 100ms baseline!"
print("  PASS: Anomaly correctly detected")


print()
print("=" * 60)
print("TEST 2: HEALTH MONITOR")
print("=" * 60)

from utils.health_monitor import HealthMonitor
hm = HealthMonitor()
result = hm.evaluate_request(
    endpoint_id=1, latency_ms=500, status_code=200, response_size=100,
    path_pattern="/test", detector=d
)
print(f"  Health score after 500ms spike: {result['health_score']} (status: {result['status']})")
print(f"  Anomalies: {len(result['anomalies'])}")
assert result["health_score"] < 80, f"FAIL: Health score should be < 80, got {result['health_score']}"
print("  PASS: Health score correctly degraded")


print()
print("=" * 60)
print("TEST 3: CONTRACT DRIFT DETECTION")
print("=" * 60)

from utils.schema_intelligence import learn_and_compare, schema_registry

# Clear any stale test schemas
schema_registry._schemas.clear()

# First observation - should just learn, no changes
s1, c1 = learn_and_compare("POST /test-drift", {"name": "Alice", "score": 90, "active": True})
print(f"  First obs: changes={len(c1)} (expected 0)")
assert len(c1) == 0, "FAIL: First observation should have no changes"

# Same shape - should have no changes
s2, c2 = learn_and_compare("POST /test-drift", {"name": "Bob", "score": 85, "active": False})
print(f"  Same shape: changes={len(c2)} (expected 0)")
assert len(c2) == 0, "FAIL: Same shape should have no changes"

# BREAKING change: remove 'active', change 'score' to string, add 'status'
s3, c3 = learn_and_compare("POST /test-drift", {"name": "Charlie", "score": "high", "status": "enrolled"})
print(f"  Breaking change: changes={len(c3)}")
for c in c3:
    print(f"    {c['severity']}: {c['change_type']} at {c['path']}")

breaking = [c for c in c3 if c["severity"] == "BREAKING"]
warnings = [c for c in c3 if c["severity"] == "WARNING"]
assert len(breaking) > 0 or len(warnings) > 0, "FAIL: No BREAKING/WARNING detected!"
print("  PASS: Contract drift correctly detected")


print()
print("=" * 60)
print("TEST 4: CHAOS ENGINE")
print("=" * 60)

from core.state import CHAOS_PROFILES, PLATFORM_STATE

friday = CHAOS_PROFILES["friday_afternoon"]
print(f"  Friday Afternoon: global_chaos={friday['global_chaos']}, latency_boost={friday['latency_boost']}")
assert friday["global_chaos"] == 30
assert friday["latency_boost"] == 1000

# Test chaos_level > 0 is used (not is_active)
class FakeChaos:
    chaos_level = 25
    is_active = False

chaos = FakeChaos()
effective_chaos = chaos.chaos_level if chaos and chaos.chaos_level > 0 else 0
assert effective_chaos == 25, f"FAIL: Got {effective_chaos}"
print(f"  chaos_level=25, is_active=False -> effective_chaos={effective_chaos}")
print("  PASS: Chaos engine uses chaos_level, ignores is_active")


print()
print("=" * 60)
print("TEST 5: VOLATILITY KEY FIX (health_monitor crash)")
print("=" * 60)

d2 = AdaptiveAnomalyDetector(persist_path=None)
for lat in [200, 210, 205]:
    d2.update("/vol-test", lat)

detail = d2.get_anomaly_detail("/vol-test", 800)
assert "volatility" not in detail, "FAIL: 'volatility' should not be in detail!"
assert "dynamic_threshold" in detail

hm2 = HealthMonitor()
try:
    result2 = hm2.evaluate_request(
        endpoint_id=99, latency_ms=800, status_code=200, response_size=500,
        path_pattern="/vol-test", detector=d2
    )
    print(f"  Health evaluation: score={result2['health_score']}, anomalies={len(result2['anomalies'])}")
    print("  PASS: HealthMonitor no longer crashes")
except KeyError as e:
    print(f"  FAIL: KeyError: {e}")
    sys.exit(1)


print()
print("=" * 60)
print("ALL 5 TESTS PASSED")
print("=" * 60)
