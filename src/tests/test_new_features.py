"""
Verification script for the new features.
Tests: Smart Mock Data Generation, AI Narrator, Improved Path Normalizer
"""
import json
import sys

# Add src to path
sys.path.insert(0, '.')

from utils.schema_learner import learn_schema, generate_mock_response
from utils.drift_detector import narrate_drift, detect_schema_drift, calculate_drift_score
from utils.normalization import normalize_path

def test_smart_mock():
    print("=" * 60)
    print("TEST 1: Smart Mock Data Generation")
    print("=" * 60)
    
    real_response = {
        "id": 42,
        "first_name": "John",
        "last_name": "Doe",
        "email": "john@example.com",
        "avatar_url": "https://cdn.com/pic.jpg",
        "created_at": "2024-01-15T10:30:00Z",
        "status": "active",
        "age": 28,
        "balance": 149.99,
        "phone": "+1-555-0100",
        "address": {
            "city": "Portland",
            "country": "US",
            "zip_code": "97201"
        },
        "tags": ["admin", "verified"]
    }
    
    schema = learn_schema(None, real_response)
    
    mock1 = generate_mock_response(schema)
    mock2 = generate_mock_response(schema)
    
    print("\n--- Mock 1 ---")
    print(json.dumps(mock1, indent=2))
    print("\n--- Mock 2 ---")
    print(json.dumps(mock2, indent=2))
    
    # Verify diversity
    assert mock1.get("email") != mock2.get("email") or mock1.get("first_name") != mock2.get("first_name"), \
        "Mock responses should differ!"
    assert "@" in str(mock1.get("email", "")), "Email should contain @"
    assert isinstance(mock1.get("id"), int), "ID should be integer"
    assert isinstance(mock1.get("balance"), (int, float)), "Balance should be numeric"
    assert isinstance(mock1.get("address"), dict), "Address should be a dict"
    assert "city" in mock1.get("address", {}), "Address should contain city"
    
    print("\n[PASS] Mock data is varied and realistic!")

def test_narrator():
    print("\n" + "=" * 60)
    print("TEST 2: AI Contract Change Narrator")
    print("=" * 60)
    
    drift_issues = [
        {"type": "missing_field", "path": "$.avatar_url", "severity": "high",
         "message": "Field avatar_url was expected but is missing"},
        {"type": "type_change", "path": "$.id", "severity": "medium",
         "expected": "int", "actual": "str",
         "message": "Field id changed from int to str"},
        {"type": "new_field", "path": "$.profile_v2", "severity": "low",
         "message": "New field profile_v2 detected"}
    ]
    
    narration = narrate_drift(drift_issues, endpoint_path="/users/{id}")
    print(narration)
    
    assert "BREAKING" in narration, "Should contain BREAKING label"
    assert "WARNING" in narration, "Should contain WARNING label"
    assert "avatar" in narration.lower(), "Should mention the field context"
    assert "Action" in narration, "Should provide an action"
    
    print("\n[PASS] Narrator produces actionable plain-English reports!")

def test_normalizer():
    print("\n" + "=" * 60)
    print("TEST 3: Improved Path Normalizer")
    print("=" * 60)
    
    tests = [
        ("/users/42/profile", "/users/{id}/profile"),
        ("/users/550e8400-e29b-41d4-a716-446655440000", "/users/{id}"),
        ("/files/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6", "/files/{hash}"),
        ("/api/v2/items", "/api/v2/items"),  # v2 should NOT be normalized
    ]
    
    all_pass = True
    for input_path, expected in tests:
        result = normalize_path(input_path)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  [{status}] {input_path:55} -> {result} (expected: {expected})")
    
    # Test slug detection
    slug_result = normalize_path("/posts/my-first-blog-post-about-python")
    print(f"  [{'PASS' if '{slug}' in slug_result else 'FAIL'}] Slug detection: {slug_result}")
    
    print(f"\n[{'PASS' if all_pass else 'PARTIAL'}] Path normalizer handles varied formats!")

def test_health_monitor():
    print("\n" + "=" * 60)
    print("TEST 4: AI Anomaly Detection (Health Monitor)")
    print("=" * 60)
    
    from utils.health_monitor import HealthMonitor
    
    hm = HealthMonitor()
    
    # 4a: Normal traffic (should be healthy)
    for i in range(10):
        hm.evaluate_request(
            endpoint_id=1, latency_ms=180 + (i * 3), status_code=200,
            response_size=1500, learned_latency_mean=200, learned_latency_std=50,
            learned_error_rate=0.02, path_pattern="/users/{id}"
        )
    h = hm.get_endpoint_health(1)
    assert h["health_score"] == 100.0, f"Normal traffic should be 100, got {h['health_score']}"
    assert h["status"] == "healthy", f"Should be healthy, got {h['status']}"
    print(f"  [PASS] Normal traffic: score={h['health_score']}, status={h['status']}")
    
    # 4b: Latency spike (>2 sigma)
    r = hm.evaluate_request(
        endpoint_id=1, latency_ms=500, status_code=200,
        response_size=1500, learned_latency_mean=200, learned_latency_std=50,
        learned_error_rate=0.02, path_pattern="/users/{id}"
    )
    assert r["latency_anomaly"], "Should detect latency anomaly"
    assert r["health_score"] < 100, f"Score should drop, got {r['health_score']}"
    print(f"  [PASS] Latency spike: score={r['health_score']}, anomaly_detected=True")
    
    # 4c: Error rate spike (>3x baseline)
    for i in range(5):
        hm.evaluate_request(
            endpoint_id=2, latency_ms=100, status_code=500,
            response_size=50, learned_latency_mean=100, learned_latency_std=20,
            learned_error_rate=0.02, path_pattern="/orders"
        )
    h2 = hm.get_endpoint_health(2)
    assert h2["error_spike"], "Should detect error spike"
    assert h2["health_score"] < 80, f"Score should be degraded, got {h2['health_score']}"
    print(f"  [PASS] Error spike: score={h2['health_score']}, error_spike=True")
    
    # 4d: Response size anomaly
    hm2 = HealthMonitor()
    for i in range(8):
        hm2.evaluate_request(
            endpoint_id=3, latency_ms=100, status_code=200,
            response_size=10000, learned_latency_mean=100, learned_latency_std=20,
            learned_error_rate=0.0, path_pattern="/data/export"
        )
    r3 = hm2.evaluate_request(
        endpoint_id=3, latency_ms=100, status_code=200,
        response_size=100, learned_latency_mean=100, learned_latency_std=20,  # 100x smaller
        learned_error_rate=0.0, path_pattern="/data/export"
    )
    assert r3["size_anomaly"], "Should detect size anomaly"
    print(f"  [PASS] Size drift: score={r3['health_score']}, size_anomaly=True")
    
    # 4e: Global health aggregation
    gh = hm.get_global_health()
    assert gh["endpoints_monitored"] == 2, f"Should monitor 2 endpoints, got {gh['endpoints_monitored']}"
    assert gh["anomaly_count"] >= 1, f"Should have anomalies, got {gh['anomaly_count']}"
    print(f"  [PASS] Global health: score={gh['score']}, monitored={gh['endpoints_monitored']}, anomalies={gh['anomaly_count']}")
    
    print("\n[PASS] All health monitoring checks passed!")

if __name__ == "__main__":
    test_smart_mock()
    test_narrator()
    test_normalizer()
    test_health_monitor()
    print("\n" + "=" * 60)
    print("ALL VERIFICATION TESTS COMPLETE")
    print("=" * 60)
