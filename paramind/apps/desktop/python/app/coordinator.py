from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CoordinatorState:
    def __init__(self):
        self._lock = Lock()
        self._peers: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._event_id = 0

    def _append_event(
        self,
        event_type: str,
        entity_id: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._event_id += 1
            event = {
                "id": self._event_id,
                "type": event_type,
                "entity_id": entity_id,
                "conversation_id": conversation_id,
                "timestamp": utcnow().isoformat(),
                "payload": payload,
            }
            self._events.append(event)
            return event

    def _normalize_peer(self, peer: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            "id": str(peer["id"]),
            "display_name": str(peer.get("display_name") or peer["id"]),
            "backend_port": int(peer.get("backend_port") or 0),
            "status": str(peer.get("status") or "online"),
            "capabilities": dict(peer.get("capabilities") or {}),
            "last_seen_at": peer.get("last_seen_at") or utcnow().isoformat(),
            "updated_at": utcnow().isoformat(),
        }
        return normalized

    def register_peer(self, peer: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_peer(peer)
        with self._lock:
            self._peers[normalized["id"]] = normalized
        self._append_event("peer.joined", normalized["id"], {"peer": normalized})
        return normalized

    def heartbeat_peer(self, peer: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_peer(peer)
        with self._lock:
            previous = self._peers.get(normalized["id"], {})
            normalized["status"] = previous.get("status", "online")
            self._peers[normalized["id"]] = normalized
        self._append_event("peer.updated", normalized["id"], {"peer": normalized})
        return normalized

    def leave_peer(self, peer_id: str) -> dict[str, Any] | None:
        with self._lock:
            peer = self._peers.get(peer_id)
            if peer is None:
                return None
            peer = dict(peer)
            peer["status"] = "offline"
            peer["updated_at"] = utcnow().isoformat()
            self._peers[peer_id] = peer
        self._append_event("peer.left", peer_id, {"peer": peer})
        return peer

    def list_peers(self) -> list[dict[str, Any]]:
        with self._lock:
            peers = list(self._peers.values())
        return sorted(peers, key=lambda item: item["display_name"])

    def publish_event(
        self,
        event_type: str,
        entity_id: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        return self._append_event(event_type, entity_id, payload, conversation_id)

    def list_events(self, after: int = 0, limit: Optional[int] = None) -> list[dict[str, Any]]:
        with self._lock:
            events = [event for event in self._events if event["id"] > after]
        if limit is not None:
            return events[:limit]
        return events

    def snapshot(self) -> dict[str, Any]:
        return {"peers": self.list_peers(), "events": self.list_events()}


def create_coordinator_app(state: CoordinatorState | None = None) -> FastAPI:
    state = state or CoordinatorState()
    app = FastAPI(title="ParaMind Localhost Coordinator")

    @app.get("/api/health")
    def health():
        return {"status": "healthy", "transport": "localhost-coordinator"}

    @app.get("/api/peers")
    def list_peers():
        return state.list_peers()

    @app.post("/api/peers/register")
    def register_peer(payload: dict[str, Any]):
        if "id" not in payload:
            raise HTTPException(status_code=400, detail="id is required")
        return state.register_peer(payload)

    @app.post("/api/peers/heartbeat")
    def heartbeat_peer(payload: dict[str, Any]):
        if "id" not in payload:
            raise HTTPException(status_code=400, detail="id is required")
        return state.heartbeat_peer(payload)

    @app.post("/api/peers/leave")
    def leave_peer(payload: dict[str, Any]):
        peer_id = str(payload.get("id") or "").strip()
        if not peer_id:
            raise HTTPException(status_code=400, detail="id is required")
        peer = state.leave_peer(peer_id)
        if peer is None:
            raise HTTPException(status_code=404, detail="peer not found")
        return peer

    @app.get("/api/events")
    def list_events(after: int = 0, limit: Optional[int] = None):
        return state.list_events(after=after, limit=limit)

    @app.post("/api/events")
    def publish_event(payload: dict[str, Any]):
        event_type = str(payload.get("type") or "").strip()
        entity_id = str(payload.get("entity_id") or "").strip()
        if not event_type or not entity_id:
            raise HTTPException(status_code=400, detail="type and entity_id are required")
        return state.publish_event(
            event_type=event_type,
            entity_id=entity_id,
            payload=dict(payload.get("payload") or {}),
            conversation_id=payload.get("conversation_id"),
        )

    @app.websocket("/ws/coordinator")
    async def websocket_coordinator(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"type": "snapshot", **state.snapshot()})
        last_seen = 0
        try:
            while True:
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
                except asyncio.TimeoutError:
                    message = None
                except WebSocketDisconnect:
                    break

                if message:
                    action = str(message.get("type") or "").strip()
                    payload = dict(message.get("payload") or {})
                    if action == "register":
                        peer = state.register_peer(payload)
                        await websocket.send_json({"type": "peer.joined", "peer": peer})
                    elif action == "heartbeat":
                        peer = state.heartbeat_peer(payload)
                        await websocket.send_json({"type": "peer.updated", "peer": peer})
                    elif action == "leave":
                        peer_id = str(payload.get("id") or "").strip()
                        peer = state.leave_peer(peer_id)
                        if peer is not None:
                            await websocket.send_json({"type": "peer.left", "peer": peer})
                    elif action == "publish":
                        event = state.publish_event(
                            event_type=str(payload.get("event_type") or "").strip(),
                            entity_id=str(payload.get("entity_id") or "").strip(),
                            payload=dict(payload.get("payload") or {}),
                            conversation_id=payload.get("conversation_id"),
                        )
                        await websocket.send_json(event)
                    elif action == "snapshot":
                        await websocket.send_json({"type": "snapshot", **state.snapshot()})
                    else:
                        await websocket.send_json({"type": "error", "error": f"Unknown action: {action}"})

                events = state.list_events(after=last_seen)
                for event in events:
                    last_seen = event["id"]
                    await websocket.send_json(event)
        finally:
            await websocket.close()

    return app


def main():
    import uvicorn
    import os

    app = create_coordinator_app()
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PARAMIND_COORDINATOR_PORT", "9010")),
    )


if __name__ == "__main__":
    main()
