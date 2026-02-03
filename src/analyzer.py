import json
import statistics
from datetime import datetime
import os

def analyze_logs():
    # Paths
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    LOG_FILE = os.path.join(BASE_DIR, '..', 'data', 'production_logs.json')
    MODEL_FILE = os.path.join(BASE_DIR, '..', 'data', 'behavior_model.json')
    
    try:
        with open(LOG_FILE, "r") as f:
            logs = json.load(f)
    except FileNotFoundError:
        print(f"Error: {LOG_FILE} not found. Run generate_logs.py first.")
        return

    total_requests = len(logs)
    if total_requests == 0:
        print("No logs to analyze.")
        return

    error_count = 0
    friday_latencies = []
    normal_latencies = []
    SLOW_THRESHOLD = 500
    slow_count_friday = 0
    total_friday = 0
    slow_count_normal = 0
    total_normal = 0
    all_success_latencies = []

    for log in logs:
        if log["status"] >= 500:
            error_count += 1
            continue
            
        latency = log["latency_ms"]
        all_success_latencies.append(latency)
        dt = datetime.fromisoformat(log["timestamp"])
        is_friday = dt.weekday() == 4
        
        if is_friday:
            total_friday += 1
            if latency > SLOW_THRESHOLD:
                slow_count_friday += 1
            friday_latencies.append(latency)
        else:
            total_normal += 1
            if latency > SLOW_THRESHOLD:
                slow_count_normal += 1
            normal_latencies.append(latency)

    error_rate = error_count / total_requests
    prob_slow_given_friday = slow_count_friday / total_friday if total_friday > 0 else 0
    prob_slow_given_normal = slow_count_normal / total_normal if total_normal > 0 else 0
    
    base_latencies = [l for l in all_success_latencies if l <= SLOW_THRESHOLD]
    slow_latencies = [l for l in all_success_latencies if l > SLOW_THRESHOLD]
    
    avg_base_latency = statistics.mean(base_latencies) if base_latencies else 0
    stdev_base_latency = statistics.stdev(base_latencies) if len(base_latencies) > 1 else 0
    avg_slow_latency = statistics.mean(slow_latencies) if slow_latencies else 0
    stdev_slow_latency = statistics.stdev(slow_latencies) if len(slow_latencies) > 1 else 0

    model = {
        "error_rate": round(error_rate, 4),
        "prob_slow_friday": round(prob_slow_given_friday, 4),
        "prob_slow_normal": round(prob_slow_given_normal, 4),
        "base_latency": {
            "mean": round(avg_base_latency, 2),
            "stdev": round(stdev_base_latency, 2)
        },
        "slow_latency": {
            "mean": round(avg_slow_latency, 2),
            "stdev": round(stdev_slow_latency, 2)
        },
        "total_analyzed": total_requests
    }

    print("Analysis Complete. Model:", json.dumps(model, indent=2))
    
    with open(MODEL_FILE, "w") as f:
        json.dump(model, f, indent=2)

if __name__ == "__main__":
    analyze_logs()
