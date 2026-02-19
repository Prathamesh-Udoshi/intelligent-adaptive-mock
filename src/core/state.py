"""
Global State
=============
All mutable global state, constants, chaos profiles, buffers, and locks.
Centralizing these prevents circular imports and makes state explicit.
"""

import os
import asyncio
from typing import List, Dict

from utils.health_monitor import HealthMonitor

# ── Target URL ──
TARGET_URL = os.environ.get("TARGET_URL", "http://httpbin.org")

# ── Platform State ──
PLATFORM_STATE = {
    "mode": "proxy",           # "proxy" or "mock"
    "learning_enabled": True,
    "active_chaos_profile": "normal"
}

# ── Chaos Profiles ──
CHAOS_PROFILES = {
    "normal": {
        "name": "Normal Operations",
        "description": "Standard behavior based on learned patterns.",
        "global_chaos": 0,
        "latency_boost": 0,
        "corrupt_responses": False
    },
    "friday_afternoon": {
        "name": "Friday Afternoon",
        "description": "High latency and frequent random errors.",
        "global_chaos": 30,
        "latency_boost": 1000,
        "corrupt_responses": False
    },
    "db_bottleneck": {
        "name": "Database Bottleneck",
        "description": "POST/PUT/PATCH requests are extremely slow.",
        "global_chaos": 0,
        "latency_boost_methods": {"POST": 5000, "PUT": 5000, "PATCH": 5000},
        "corrupt_responses": False
    },
    "zombie_api": {
        "name": "Zombie API",
        "description": "200 OK status codes but with empty or corrupted payloads.",
        "global_chaos": 0,
        "corrupt_responses": True
    }
}

# ── Learning Buffer ──
LEARNING_BUFFER_SIZE = 1
LEARNING_BUFFER: List[Dict] = []
buffer_lock = asyncio.Lock()

# ── Recent Logs (last 50 requests) ──
RECENT_LOGS: List[Dict] = []
logs_lock = asyncio.Lock()

# ── Health Monitor (AI Anomaly Detection) ──
health_monitor = HealthMonitor()
