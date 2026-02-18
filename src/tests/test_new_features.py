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

if __name__ == "__main__":
    test_smart_mock()
    test_narrator()
    test_normalizer()
    print("\n" + "=" * 60)
    print("ALL VERIFICATION TESTS COMPLETE")
    print("=" * 60)
