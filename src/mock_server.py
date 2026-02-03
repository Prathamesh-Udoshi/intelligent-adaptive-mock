import json
import random
import time
import datetime
import os
import requests
from flask import Flask, request, jsonify, send_file, make_response
import threading

app = Flask(__name__)

# Config
# Default to httpbin base for generic testing
TARGET_URL = os.environ.get("TARGET_URL", "http://httpbin.org") 
LEARNING_MODE = False
CHAOS_LEVEL = 0
REAL_REQUEST_BUFFER = []

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, '..', 'data')
STATIC_DIR = os.path.join(BASE_DIR, '..', 'static')
MODEL_FILE = os.path.join(DATA_DIR, "behavior_model.json")

# Load Model
try:
    with open(MODEL_FILE, "r") as f:
        MODEL = json.load(f)
        print("Loaded behavior model:", MODEL)
except FileNotFoundError:
    print(f"WARNING: {MODEL_FILE} not found. Using defaults.")
    MODEL = {
        "error_rate": 0.1,
        "prob_slow_friday": 0.1,
        "prob_slow_normal": 0.05,
        "base_latency": {"mean": 200, "stdev": 50},
        "slow_latency": {"mean": 1000, "stdev": 200}
    }

def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = '*'
    response.headers['Access-Control-Allow-Methods'] = '*'
    return response

@app.route('/', methods=['GET'])
def get_dashboard():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return send_file(index_path)
    return "<h1>Dashboard not found</h1>"

# --- Admin API ---

@app.route('/admin/chaos', methods=['POST', 'OPTIONS'])
def set_chaos():
    if request.method == 'OPTIONS':
        return add_cors_headers(make_response())
    
    global CHAOS_LEVEL
    data = request.json
    if data and 'level' in data:
        CHAOS_LEVEL = int(data['level'])
        return add_cors_headers(jsonify({"status": "updated", "chaos_level": CHAOS_LEVEL}))
    return add_cors_headers(jsonify({"error": "Invalid data"}), 400)

@app.route('/admin/config', methods=['GET'])
def get_config():
    return jsonify({
        "chaos_level": CHAOS_LEVEL,
        "learning_mode": LEARNING_MODE,
        "target_url": TARGET_URL
    })

@app.route('/admin/learning', methods=['POST'])
def set_learning():
    global LEARNING_MODE
    data = request.json
    if data and 'enabled' in data:
        LEARNING_MODE = bool(data['enabled'])
        return jsonify({"status": "updated", "learning_mode": LEARNING_MODE})
    return jsonify({"error": "Invalid data"}), 400

# --- Core Logic ---

# Catch-all route for any subpath and any method
@app.route('/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
def handle_universal_request(subpath):
    if request.method == 'OPTIONS':
        return add_cors_headers(make_response())

    mock_enabled = request.headers.get("X-Mock-Enabled", "false").lower() == "true"
    
    # 1. PROXY MODE
    if not mock_enabled:
        start_time = time.time()
        try:
            # Construct Target URL
            target_full_url = f"{TARGET_URL}/{subpath}"
            
            # Forward Request
            # We use request.get_data() to allow binary bodies too
            resp = requests.request(
                method=request.method,
                url=target_full_url,
                headers={k:v for k,v in request.headers if k != 'Host'},
                data=request.get_data(),
                params=request.args,
                allow_redirects=False
            )
            
            latency_ms = (time.time() - start_time) * 1000
            
            if LEARNING_MODE:
                REAL_REQUEST_BUFFER.append({
                    "timestamp": datetime.datetime.now().isoformat(),
                    "status": resp.status_code,
                    "latency_ms": latency_ms
                })
                if len(REAL_REQUEST_BUFFER) >= 50:
                    threading.Thread(target=learn_from_buffer).start()

            flask_resp = make_response(resp.content, resp.status_code)
            for k,v in resp.headers.items():
                if k not in ['Content-Length', 'Transfer-Encoding', 'Content-Encoding']:
                     flask_resp.headers[k] = v
            return add_cors_headers(flask_resp)
            
        except Exception as e:
            return add_cors_headers(jsonify({"error": f"Proxy Error to {target_full_url}: {str(e)}"}), 502)

    # 2. MOCK MODE
    # For now, apply the generic chaos logic to ALL endpoints.
    # ideally, we would look up specific mock data for 'subpath'.
    
    req_chaos = CHAOS_LEVEL
    if request.args.get('chaos'):
        try:
            req_chaos = int(request.args.get('chaos'))
        except ValueError:
            pass
    elif request.headers.get('x-chaos-level'):
        try:
            req_chaos = int(request.headers.get('x-chaos-level'))
        except ValueError:
            pass
            
    is_friday = datetime.datetime.now().weekday() == 4
    current_error_prob = MODEL["error_rate"] + (req_chaos / 100.0)
    
    if random.random() < current_error_prob:
        resp = make_response(jsonify({"error": f"Simulated Failure for /{subpath}"}), 500)
        return add_cors_headers(resp)
        
    current_slow_prob = MODEL["prob_slow_friday"] if is_friday else MODEL["prob_slow_normal"]
    current_slow_prob += (req_chaos / 100.0)
    
    if random.random() < current_slow_prob:
        mu = MODEL["slow_latency"]["mean"]
        sigma = MODEL["slow_latency"]["stdev"]
        latency = random.normalvariate(mu, sigma)
    else:
        mu = MODEL["base_latency"]["mean"]
        sigma = MODEL["base_latency"]["stdev"]
        latency = random.normalvariate(mu, sigma)
        
    latency = max(10, latency)
    time.sleep(latency / 1000.0)
    
    # Generic Success Response
    resp = jsonify({
        "status": "success",
        "mock_path": subpath,
        "method": request.method,
        "simulated_latency_ms": round(latency, 2),
        "chaos_level_applied": req_chaos,
        "source": "AI Mock (Universal)"
    })
    return add_cors_headers(resp)

def learn_from_buffer():
    global REAL_REQUEST_BUFFER
    global MODEL
    
    if not REAL_REQUEST_BUFFER:
        return
        
    batch = REAL_REQUEST_BUFFER[:]
    REAL_REQUEST_BUFFER = []
    
    print(f"Learning from {len(batch)} real requests...")
    
    alpha = 0.2
    
    errors = [1 if r['status'] >= 500 else 0 for r in batch]
    latencies = [r['latency_ms'] for r in batch if r['status'] < 500]
    
    if not latencies:
        return

    batch_error_rate = sum(errors) / len(batch)
    batch_mean_latency = sum(latencies) / len(latencies)
    
    new_error_rate = (MODEL['error_rate'] * (1-alpha)) + (batch_error_rate * alpha)
    new_mean_latency = (MODEL['base_latency']['mean'] * (1-alpha)) + (batch_mean_latency * alpha)
    
    MODEL['error_rate'] = round(new_error_rate, 4)
    MODEL['base_latency']['mean'] = round(new_mean_latency, 2)
    
    temp_file = MODEL_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(MODEL, f, indent=2)
    os.replace(temp_file, MODEL_FILE)
    print("Model updated!", MODEL)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
