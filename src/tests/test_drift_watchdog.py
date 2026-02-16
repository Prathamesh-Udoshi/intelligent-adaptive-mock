import requests
import time
import sys

BASE_URL = "http://localhost:8000"

def log_stage(stage):
    print(f"\n{'='*10} STAGE: {stage} {'='*10}")
    sys.stdout.flush()

def log_step(step, success=True):
    icon = "CHECK" if success else "WAIT"
    print(f"  [{icon}] {step}")
    sys.stdout.flush()

def test_drift():
    ep_id = None
    try:
        log_stage("INITIALIZATION")
        log_step("Setting platform to Proxy mode")
        requests.post(f"{BASE_URL}/admin/mode", json={"mode": "proxy"}, timeout=5)
        
        log_step("Enabling learning engine")
        requests.post(f"{BASE_URL}/admin/learning", json={"enabled": True}, timeout=5)

        # Step 1: Train
        log_stage("LEARNING PHASE")
        path = "/json"
        log_step(f"Requesting real API {path} to learn patterns")
        requests.get(f"{BASE_URL}{path}", timeout=5)
        time.sleep(1.5) # Wait for background buffer processing
        
        # Step 2: Get Endpoint ID
        log_step("Identifying learned endpoint in database")
        eps = requests.get(f"{BASE_URL}/admin/endpoints", timeout=5).json()
        for ep in eps:
            if ep['path_pattern'] == path:
                ep_id = ep['id']
                break
        
        if not ep_id:
            log_step("FAILED: Endpoint not detected yet. Learning buffer might be full.", False)
            return

        # Step 3: Sabotage (Simulation)
        log_stage("CONTRACT SABOTAGE (Verification Setup)")
        log_step(f"Fetching learned schema for ID: {ep_id}")
        stats = requests.get(f"{BASE_URL}/admin/endpoints/{ep_id}/stats", timeout=5).json()
        schema = stats['behavior']['schema_preview'] or {}
        
        log_step("Injecting an impossible requirement into the contract")
        schema["mandatory_legacy_token"] = "string"
        requests.post(f"{BASE_URL}/admin/endpoints/{ep_id}/schema", json={
            "type": "outbound",
            "schema": schema
        }, timeout=5)

        # Step 4: Trigger
        log_stage("WATCHDOG TRIGGER")
        log_step("Making a fresh request (Real response will now violate the contract)")
        requests.get(f"{BASE_URL}{path}", timeout=5)
        
        log_step("Waiting for Watchdog background analysis...")
        time.sleep(2)
        
        # Step 5: Check
        log_stage("VERIFICATION")
        alerts = requests.get(f"{BASE_URL}/admin/drift-alerts?unresolved_only=true", timeout=5).json()
        target_alert = next((a for a in alerts if a['endpoint_id'] == ep_id), None)
        
        if target_alert:
            log_step(f"SUCCESS: Alert detected for {path}!")
            print(f"      Summary: {target_alert['drift_summary']}")
            
            details = target_alert.get('drift_details', [])
            if isinstance(details, str):
                import json
                details = json.loads(details)
            
            print(f"      Detailed Issues Found ({len(details)}):")
            for i, d in enumerate(details, 1):
                severity_icon = "🔴" if d['severity'] == "high" else "🟡"
                print(f"        {i}. {severity_icon} {d['type'].upper()} at {d['path']}")
                print(f"           Message: {d['message']}")
        else:
            log_step("FAILED: No drift alert found in dashboard.", False)

        # Step 6: Cleanup (Crucial!)
        log_stage("DATABASE CLEANUP")
        log_step(f"Deleting test history to keep project_b.db clean")
        # Note: We don't have a specific 'delete endpoint' API yet, 
        # but we can resolve the alert so it disappears from the 'unresolved' list.
        if target_alert:
            requests.post(f"{BASE_URL}/admin/drift-alerts/{target_alert['id']}/resolve")
            log_step("Test alert marked as Resolved.")

    except Exception as e:
        print(f"\n[!] CRITICAL ERROR: {str(e)}")

if __name__ == "__main__":
    test_drift()
    print("\n" + "="*40)
    print("TEST FINISHED. DATABASE STATE PRESERVED.")
    print("="*40)
