from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlmodel import Session, select

from .coordinator_client import CoordinatorClient
from .models import Peer, utcnow


class MockP2PAdapter:
    def __init__(self, settings, transport: Any | None = None):
        self.settings = settings
        self.transport = transport if transport is not None else self._build_transport(settings)

    def _build_transport(self, settings):
        if getattr(settings, "transport", "mock-local") != "localhost-coordinator":
            return None
        coordinator_url = getattr(settings, "coordinator_base_url", None)
        if not coordinator_url:
            return None
        try:
            return CoordinatorClient(coordinator_url)
        except Exception:
            return None

    def _peer_payload(self, peer: Peer) -> dict[str, Any]:
        return {
            "id": peer.id,
            "display_name": peer.display_name,
            "backend_port": peer.backend_port,
            "status": peer.status,
            "capabilities": json.loads(peer.capabilities_json or "{}"),
            "last_seen_at": peer.last_seen_at.isoformat() if peer.last_seen_at else None,
        }

    def _serialize_local_peer(self, session: Session) -> dict[str, Any]:
        peer = session.get(Peer, self.settings.instance_id)
        if peer is None:
            peer = Peer(
                id=self.settings.instance_id,
                display_name=self.settings.instance_name,
                backend_port=self.settings.backend_port,
                status="online",
                capabilities_json=json.dumps(self._capabilities()),
                last_seen_at=utcnow(),
                updated_at=utcnow(),
            )
        return self._peer_payload(peer)

    def _capabilities(self) -> dict[str, Any]:
        return {
            "transport": "localhost-coordinator" if self.transport is not None else "local-db",
            "model_id": self.settings.model_id,
            "family": self.settings.family,
            "device": self.settings.device,
        }

    def _sync_remote_peers(self, session: Session):
        if self.transport is None:
            return

        remote_peers = self.transport.list_peers()
        if remote_peers is None:
            return
        local_by_id = {peer.id: peer for peer in session.exec(select(Peer)).all()}
        seen_remote_ids = set()

        for remote in remote_peers:
            remote_id = str(remote["id"])
            seen_remote_ids.add(remote_id)
            peer = local_by_id.get(remote_id)
            capabilities = remote.get("capabilities") or {}
            if peer is None:
                peer = Peer(
                    id=remote_id,
                    display_name=str(remote.get("display_name") or remote_id),
                    backend_port=int(remote.get("backend_port") or 0),
                    status=str(remote.get("status") or "online"),
                    capabilities_json=json.dumps(capabilities),
                    last_seen_at=utcnow(),
                    updated_at=utcnow(),
                )
                session.add(peer)
            else:
                peer.display_name = str(remote.get("display_name") or remote_id)
                peer.backend_port = int(remote.get("backend_port") or 0)
                peer.status = str(remote.get("status") or "online")
                peer.capabilities_json = json.dumps(capabilities)
                peer.last_seen_at = utcnow()
                peer.updated_at = utcnow()

        for peer in local_by_id.values():
            if peer.id != self.settings.instance_id and peer.id not in seen_remote_ids:
                peer.status = "offline"
                peer.updated_at = utcnow()

    def upsert_self(self, session: Session):
        now = utcnow()
        peer = session.get(Peer, self.settings.instance_id)
        capabilities = json.dumps(self._capabilities())

        if peer is None:
            peer = Peer(
                id=self.settings.instance_id,
                display_name=self.settings.instance_name,
                backend_port=self.settings.backend_port,
                status="online",
                capabilities_json=capabilities,
                last_seen_at=now,
                updated_at=now,
            )
            session.add(peer)
            if self.transport is not None:
                self.transport.register_peer(self._peer_payload(peer))
            return "joined", peer

        previous_status = peer.status
        peer.display_name = self.settings.instance_name
        peer.backend_port = self.settings.backend_port
        peer.status = "online"
        peer.capabilities_json = capabilities
        peer.last_seen_at = now
        peer.updated_at = now
        if self.transport is not None:
            self.transport.heartbeat(self._peer_payload(peer))
        return ("joined" if previous_status != "online" else "updated"), peer

    def mark_self_offline(self, session: Session):
        peer = session.get(Peer, self.settings.instance_id)
        if peer is None:
            return None
        peer.status = "offline"
        peer.updated_at = utcnow()
        if self.transport is not None:
            self.transport.leave(peer.id)
        return peer

    def sweep_stale_peers(self, session: Session):
        if self.transport is not None:
            return []
        cutoff = utcnow() - timedelta(seconds=5)
        peers = session.exec(select(Peer).where(Peer.last_seen_at < cutoff)).all()
        changed = []
        for peer in peers:
            if peer.status != "offline":
                peer.status = "offline"
                peer.updated_at = utcnow()
                changed.append(peer)
        return changed

    def list_peers(self, session: Session):
        self._sync_remote_peers(session)
        return session.exec(select(Peer).order_by(Peer.display_name)).all()

    def plan_route(self, session: Session):
        peers = [peer for peer in self.list_peers(session) if peer.status == "online"]
        if not peers:
            peers = [session.get(Peer, self.settings.instance_id)]
        peers = [peer for peer in peers if peer is not None]
        peers = sorted(peers, key=lambda peer: (peer.id != self.settings.instance_id, peer.id))
        selected = peers[: min(3, len(peers))]
        total_layers = 24
        base = total_layers // max(len(selected), 1)
        remainder = total_layers % max(len(selected), 1)
        cursor = 0
        route = []
        for index, peer in enumerate(selected):
            size = base + (1 if index < remainder else 0)
            end = cursor + size
            route.append(
                {
                    "id": peer.id,
                    "display_name": peer.display_name,
                    "layers": f"{cursor}-{end}",
                    "latency_ms": 6 + index * 7,
                    "local": peer.id == self.settings.instance_id,
                }
            )
            cursor = end

        if self.transport is not None:
            self.publish_event(
                "route.planned",
                self.settings.instance_id,
                {"route": route},
            )

        return route

    def publish_event(self, event_type: str, entity_id: str, payload: dict[str, Any], conversation_id: str | None = None):
        if self.transport is None:
            return None
        event = {
            "type": event_type,
            "entity_id": entity_id,
            "conversation_id": conversation_id,
            "payload": payload,
        }
        self.transport.publish_event(event)
        return event

    def list_events(self, after: int = 0, limit: int | None = None):
        if self.transport is None or not hasattr(self.transport, "list_events"):
            return []
        events = self.transport.list_events(after=after, limit=limit)
        return events or []
