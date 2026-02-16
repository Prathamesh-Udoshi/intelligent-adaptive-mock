import requests
import time
import json

BASE_URL = "http://localhost:8000"

def set_slider(level):
    print(f"\nSetting Chaos Slider to {level}%...")
    requests.post(f"{BASE_URL}/admin/chaos", json={"level": level})

def set_profile(profile_name):
    print(f"\nApplying Incident Profile: [{profile_name}]...")
    resp = requests.post(f"{BASE_URL}/admin/chaos/profiles", json={"profile": profile_name})
    if resp.status_code != 200:
        print(f"Failed to set profile: {resp.text}")

def test_request(method="GET", path="/api/test"):
    start = time.time()
    headers = {"X-Mock-Enabled": "true"}
    try:
        if method == "GET":
            resp = requests.get(f"{BASE_URL}{path}", headers=headers, timeout=10)
        else:
            resp = requests.post(f"{BASE_URL}{path}", headers=headers, json={"test": "data"}, timeout=10)
        
        latency = (time.time() - start) * 1000
        return resp.status_code, latency, resp.text[:50]
    except Exception as e:
        return "TIMEOUT/ERROR", 10000, str(e)

def run_test_suite():
    # Ensure we are in mock mode
    requests.post(f"{BASE_URL}/admin/mode", json={"mode": "mock"})
    
    print("STARTING CHAOS SYNERGY TEST SUITE")
    print("====================================")

    # TEST 1: Clean Slate
    set_slider(0)
    set_profile("normal")
    status, lat, body = test_request()
    print(f"NORMAL: Status={status}, Latency={lat:.0f}ms, Body={body}")

    # TEST 2: Slider Only
    set_slider(50)
    set_profile("normal")
    print("--- Running 5 requests to check error distribution (Slider 50%) ---")
    errors = 0
    for _ in range(5):
        status, lat, body = test_request()
        if status >= 500: errors += 1
        print(f"   Request: Status={status}, Latency={lat:.0f}ms")
    print(f"Results: {errors}/5 failed. Expected: ~2-3.")

    # TEST 3: Profile Overriding Slider (Friday Afternoon)
    set_slider(0)
    set_profile("friday_afternoon")
    status, lat, body = test_request()
    print(f"FRIDAY AFTERNOON: Status={status}, Latency={lat:.0f}ms (Expected > 1000ms)")

    # TEST 4: Targeted Impact (Database Bottleneck)
    set_slider(0)
    set_profile("db_bottleneck")
    print("--- Testing Selective Targeting ---")
    s1, l1, _ = test_request("GET")
    print(f"   GET Request: Latency={l1:.0f}ms (Should be FAST)")
    s2, l2, _ = test_request("POST")
    print(f"   POST Request: Latency={l2:.0f}ms (Should be > 5000ms!)")

    # TEST 5: Data Corruption (Zombie API)
    set_slider(0)
    set_profile("zombie_api")
    status, lat, body = test_request()
    print(f"ZOMBIE API: Status={status}, Latency={lat:.0f}ms, Content Snippet=[{body}]")
    if "CORRUPTED_STREAM" in body:
        print("   DEFEATED: Payload is successfully corrupted!")

    # Cleanup
    set_slider(0)
    set_profile("normal")
    print("\nTEST SUITE COMPLETE. Platform restored to Normal.")

if __name__ == "__main__":
    run_test_suite()
