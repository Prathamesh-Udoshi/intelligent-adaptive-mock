"""
Intelligent Adaptive Mock Platform
====================================
Application assembly — creates the FastAPI app and mounts all routers.

Architecture:
    core/           → Database, global state, WebSocket manager
    routers/        → API route handlers (dashboard, endpoints, drift, health, export, explorer)
    services/       → Business logic (learning, proxy)
    utils/          → Utilities (normalization, schema_learner, drift_detector, health_monitor, type_exporter)
    models.py       → SQLAlchemy ORM models
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.database import init_db
from routers import dashboard, endpoints, drift, health, export, explorer
from services import proxy

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("mock_platform")

# ── App ──
app = FastAPI(title="Intelligent Adaptive Mock Platform")

# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup ──
@app.on_event("startup")
async def startup():
    await init_db()
    
    # Start the "Brain" — background learning loop
    import asyncio
    from services.learning import process_learning_buffer
    
    async def learning_loop():
        while True:
            try:
                await process_learning_buffer()
            except Exception as e:
                logger.error(f"❌ Learning loop error: {e}")
            await asyncio.sleep(5) # Process buffer every 5 seconds
            
    asyncio.create_task(learning_loop())
    logger.info("🧠 Learning engine started (Processing every 5s)")

# ── Shutdown ──
@app.on_event("shutdown")
async def shutdown():
    from core.state import adaptive_detector
    from utils.schema_intelligence import schema_registry
    adaptive_detector.flush()
    schema_registry.flush()
    logger.info("💾 Adaptive detector baselines and schemas persisted on shutdown.")


# ── Mount Routers ──
# ORDER MATTERS: Specific routes MUST come before the catch-all proxy.
app.include_router(dashboard.router)     # /, /admin/dashboard, /admin/config, /ws, etc.
app.include_router(endpoints.router)     # /admin/endpoints/*, /admin/export-openapi
app.include_router(drift.router)         # /admin/drift-alerts/*
app.include_router(health.router)        # /admin/health/*
app.include_router(export.router)        # /admin/export-types
app.include_router(explorer.router)      # /admin/explorer/overview
app.include_router(proxy.router)         # /{path:path} — MUST BE LAST (catch-all)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
