import json
import random
import datetime
import math
import os

def generate_logs(num_logs=2000):
    logs = []
    base_time = datetime.datetime.now() - datetime.timedelta(days=30)
    
    endpoints = [
        {"path": "/api/v1/payment", "method": "POST", "base_latency": 150, "error_rate": 0.05},
        {"path": "/api/v1/users/123", "method": "GET", "base_latency": 50, "error_rate": 0.01},
        {"path": "/api/v1/users/456", "method": "GET", "base_latency": 55, "error_rate": 0.01},
        {"path": "/api/v1/products", "method": "GET", "base_latency": 200, "error_rate": 0.02},
        {"path": "/api/v1/orders", "method": "POST", "base_latency": 300, "error_rate": 0.10},
    ]

    for i in range(num_logs):
        current_time = base_time + datetime.timedelta(minutes=i*15)
        is_friday = current_time.weekday() == 4
        
        ep = random.choice(endpoints)
        status = 200
        latency = 0
        
        # Base logic
        if random.random() < ep["error_rate"]:
            status = 500
            latency = random.randint(20, 100)
        else:
            if is_friday and ep["path"] == "/api/v1/payment":
                # Special "Slow Friday" behavior for payments
                if random.random() < 0.2:
                    latency = random.normalvariate(1000, 200)
                else:
                    latency = random.normalvariate(ep["base_latency"], 20)
            else:
                latency = random.normalvariate(ep["base_latency"], 20)

        latency = max(10, latency)

        logs.append({
            "timestamp": current_time.isoformat(),
            "endpoint": ep["path"],
            "method": ep["method"],
            "status": status,
            "latency_ms": round(latency, 2)
        })

    # Path Update
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    LOG_FILE = os.path.join(BASE_DIR, '..', 'data', 'production_logs.json')

    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)
    print(f"Generated {len(logs)} logs in {LOG_FILE}")

if __name__ == "__main__":
    generate_logs()
