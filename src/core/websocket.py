"""
WebSocket Manager
==================
Manages live dashboard connections and real-time event broadcasting.
"""

import logging
from typing import List
from fastapi import WebSocket

logger = logging.getLogger("mock_platform")


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass  # Already removed (e.g., double-disconnect)

    async def broadcast(self, message: dict):
        stale_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                stale_connections.append(connection)
        # Auto-prune stale connections to prevent memory leaks
        for stale in stale_connections:
            try:
                self.active_connections.remove(stale)
            except ValueError:
                pass


# Singleton instance
manager = ConnectionManager()
