# Intelligent Adaptive Mock Platform 🧬

> **Your API's Digital Twin.** A self-learning middleware that observes real traffic, masters your API contract, and provides high-fidelity mocks with zero configuration.

---

## 🌩️ The Problem Statement

Frontend development is often held hostage by the backend. **When the backend is down, slow, or under development, the frontend team stops moving.** 
- **The "Mock Debt":** Writing manual mocks is tedious and they quickly become outdated compared to the real API.
- **Resilience Blindness:** It’s hard to test how your app handles 500 errors or high latency without actually breaking the production server.

## 💡 The Motivation

This platform acts as a **Digital Twin** for your API. It doesn't just mock; it **learns**. 
By sitting between your app and the real backend, it observes every request and response, building a real-time behavioral model. 
- **Zero-Config Mocks:** Switch from "Proxy" to "Mock" mode, and the platform takes over using learned behavior.
- **Failover-First:** If the real backend crashes, the AI instantly provides a mock fallback—your frontend never sees a "Site Cannot Be Reached" error.
- **Chaos for Quality:** Built-in "Chaos Engine" lets you inject artificial failure and latency to harden your application.
- **Contract Regression Watchdog:** Automatically detects when the backend API changes in breaking ways (missing fields, type changes) and alerts you instantly—preventing silent production failures.

---

## 🛠️ How it Works: The Learning Cycle

### 1. Inbound Intelligence
The platform detects dynamic URL segments automatically.
- **Input:** `GET /users/42`, `GET /users/89`
- **AI Normalization:** `/users/{id}` (Groups these patterns for shared statistics)

### 2. Schema Discovery
It masters the JSON structure of your requests and responses.
- **Request (Inbound):** Learns mandatory fields, data types, and nesting.
- **Response (Outbound):** Captures success/error payloads to generate realistic synthetic data.

### 3. Real-Time Visualization
Uses **WebSockets** for a zero-polling, instant-update dashboard that streams every transaction as it happens.

---

## ⚡ Quick Start (Step-By-Step)

### 1. Set up the Environment
Ensure you have Python 3.8+ installed.
```bash
# Clone and install
pip install -r requirements.txt
```

### 2. Configure and Launch
Define your target backend and a unique database for your current project.
```powershell
# Windows PowerShell Example
$env:TARGET_URL="http://localhost:8001"  # Your real API
$env:DB_NAME="project_alpha.db"          # Isolation for this project
cd src
python mock_server.py
```

### 3. Use the Dashboard
Open your browser to:
- **Control Deck:** `http://localhost:8000/` — Main panel for Chaos and Mode switching.
- **Endpoint Explorer:** `http://localhost:8000/admin/explorer` — Visual patterns and stats.
- **Interactive Docs:** `http://localhost:8000/admin/docs` — Swagger UI for learned APIs.

---

## 📸 Dashboard Preview
*(Insert Mockup Screenshot Here)*
> **Live Monitoring:** The dashboard uses WebSockets to show a real-time stream of traffic, including AI-generated mocks vs real backend responses.

---

## 🏗 System Architecture

```mermaid
graph TD
    Client[Browser / Client] -->|HTTP Request| Server[FastAPI Platform]
    
    subgraph "Mock Engine (Mock Mode)"
        Server --> Norm[Path Normalizer]
        Norm --> DB[(SQLite / SQLAlchemy)]
        DB --> Logic[Mock Logic]
        Logic -->|Simulated Latency| Gen[Synthetic Generator]
        Gen --> Client
    end
    
    subgraph "Proxy & Learning (Proxy Mode)"
        Server --> Proxy[Async Proxy / HTTPX]
        Proxy --> Target[Real Backend API]
        Target --> Proxy
        Proxy -->|Capture Cycle| Buffer[Learning Buffer]
        Proxy -->|If Down| Logic
        Buffer -->|Background Task| Learner[Behavior Learner]
        Learner -->|WebSocket Broadcast| Client
        Learner --> DB
        Proxy --> Client
    end
```

---

## 🚨 Contract Regression Watchdog

The platform continuously monitors your API for **contract drift**—when the real backend's response structure changes in ways that could break your frontend.

### What It Detects:
- **Missing Fields:** Fields that existed in the learned schema but are now absent from responses
- **New Fields:** Unexpected fields that appear in responses (low severity)
- **Type Changes:** When a field changes from `string` to `number`, `object` to `array`, etc.

### How It Works:
1. **Learning Phase:** The platform observes real API responses and builds a schema model
2. **Monitoring Phase:** Every subsequent proxy request is compared against the learned schema
3. **Alert Generation:** When drift is detected, an alert is stored with:
   - **Drift Score** (0-100): Severity of the changes
   - **Drift Summary:** Human-readable description (e.g., "2 critical issues, 1 warning")
   - **Drift Details:** Exact list of what changed and where

### Accessing Drift Alerts:
- **API Endpoint:** `GET /admin/drift-alerts?unresolved_only=true`
- **Per-Endpoint Stats:** `GET /admin/endpoints/{id}/drift-stats`
- **Resolve Alert:** `POST /admin/drift-alerts/{alert_id}/resolve`

**Use Case:** If your backend team renames `user_id` to `userId` without telling you, the Watchdog will immediately flag it as a **high-severity** drift, preventing silent production bugs.

---

## 📂 Project Structure
- **`src/mock_server.py`**: The core "Traffic Controller" with WebSocket broadcasting.
- **`src/utils/schema_learner.py`**: The "Brain" that performs recursive JSON structure analysis.
- **`src/utils/normalization.py`**: Regex-driven path grouping engine.
- **`src/utils/drift_detector.py`**: Contract Regression Watchdog engine for detecting schema drift.
- **`static/`**: High-performance Vanilla JS dashboard with WebSocket clients.

## 💡 Pro-Tip
Run a different `DB_NAME` for every project. This lets you build "Behavioral Profiles" for different microservices and switch between them instantly.
