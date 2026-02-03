import json
import random
import datetime
import math
import os

def generate_logs(num_logs=2000):
    logs = []
    base_time = datetime.datetime.now() - datetime.timedelta(days=30)
    
    for i in range(num_logs):
        current_time = base_time + datetime.timedelta(minutes=i*15)
        is_friday = current_time.weekday() == 4
        
        status = 200
        latency = 0
        
        if is_friday:
            p = random.random()
            if p < 0.10:
                status = 500
                latency = random.randint(20, 100)
            elif p < 0.20:
                status = 200
                latency = random.normalvariate(1000, 300)
            else:
                status = 200
                latency = random.normalvariate(150, 40)
        else:
            p = random.random()
            if p < 0.10:
                status = 500
                latency = random.randint(20, 100)
            else:
                status = 200
                latency = random.normalvariate(150, 40)

        latency = max(10, latency)

        logs.append({
            "timestamp": current_time.isoformat(),
            "endpoint": "/api/v1/payment",
            "method": "POST",
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
