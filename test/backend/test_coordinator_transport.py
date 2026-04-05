import socket
import time
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session
import uvicorn

from paramind.apps.desktop.python.app.coordinator import create_coordinator_app
from paramind.apps.desktop.python.app.coordinator_client import CoordinatorClient
from paramind.apps.desktop.python.app.database import create_sqlite_engine, init_db
from paramind.apps.desktop.python.app.mock_p2p import MockP2PAdapter
from paramind.apps.desktop.python.app.models import Peer


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_real_coordinator():
    port = _find_free_port()
    config = uvicorn.Config(
        create_coordinator_app(),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 3.0
    while time.time() < deadline:
        try:
            with httpx.Client(trust_env=False, timeout=0.2) as client:
                response = client.get(f"http://127.0.0.1:{port}/api/health")
                if response.status_code == 200:
                    return port
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise RuntimeError("coordinator failed to start")


def test_coordinator_registers_peers_and_broadcasts_events():
    app = create_coordinator_app()
    client = TestClient(app)

    with client.websocket_connect("/ws/coordinator") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["peers"] == []

        peer_payload = {
            "id": "peer-a",
            "display_name": "Peer A",
            "backend_port": 5101,
            "status": "online",
            "capabilities": {"device": "cpu"},
        }

        response = client.post("/api/peers/register", json=peer_payload)
        assert response.status_code == 200
        peers = client.get("/api/peers").json()
        assert len(peers) == 1
        assert peers[0]["id"] == "peer-a"

        joined = ws.receive_json()
        assert joined["type"] == "peer.joined"
        assert joined["payload"]["peer"]["id"] == "peer-a"

        event_response = client.post(
            "/api/events",
            json={
                "type": "route.planned",
                "entity_id": "peer-a",
                "payload": {"route": [{"id": "peer-a", "layers": "0-24"}]},
            },
        )
        assert event_response.status_code == 200

        fanout = ws.receive_json()
        assert fanout["type"] == "route.planned"
        assert fanout["entity_id"] == "peer-a"


def test_mock_p2p_adapter_uses_coordinator_transport_for_peer_listing_and_fanout(
    tmp_path,
):
    database_path = Path(tmp_path) / "p2p.sqlite3"
    engine = create_sqlite_engine(database_path)
    init_db(engine)

    class FakeTransport:
        def __init__(self):
            self.registered = []
            self.heartbeats = []
            self.left = []
            self.published = []

        def register_peer(self, peer):
            self.registered.append(peer)

        def heartbeat(self, peer):
            self.heartbeats.append(peer)

        def leave(self, peer_id):
            self.left.append(peer_id)

        def list_peers(self):
            return [
                {
                    "id": "peer-a",
                    "display_name": "Peer A",
                    "backend_port": 5101,
                    "status": "online",
                    "capabilities": {"device": "cpu"},
                    "last_seen_at": "2026-03-24T00:00:00+00:00",
                },
                {
                    "id": "peer-b",
                    "display_name": "Peer B",
                    "backend_port": 5102,
                    "status": "online",
                    "capabilities": {"device": "cpu"},
                    "last_seen_at": "2026-03-24T00:00:00+00:00",
                },
            ]

        def publish_event(self, event):
            self.published.append(event)

        def list_events(self, after=0):
            return []

    fake_transport = FakeTransport()
    settings = SimpleNamespace(
        instance_id="peer-local",
        instance_name="Local Node",
        backend_port=5001,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family="qwen",
        device="cpu",
        transport="localhost-coordinator",
        coordinator_base_url="http://localhost:9000",
    )
    adapter = MockP2PAdapter(settings, transport=fake_transport)

    with Session(engine) as session:
        status, peer = adapter.upsert_self(session)
        assert status == "joined"
        assert peer.id == "peer-local"

        peers = adapter.list_peers(session)
        assert {item.id for item in peers} == {"peer-local", "peer-a", "peer-b"}

        route = adapter.plan_route(session)
        assert len(route) == 3
        assert fake_transport.published[-1]["type"] == "route.planned"
        assert fake_transport.registered[-1]["id"] == "peer-local"

        adapter.mark_self_offline(session)
        assert fake_transport.left[-1] == "peer-local"


def test_mock_p2p_adapter_does_not_mark_coordinator_peers_offline_locally(tmp_path):
    database_path = Path(tmp_path) / "stability.sqlite3"
    engine = create_sqlite_engine(database_path)
    init_db(engine)

    class FakeTransport:
        def register_peer(self, peer):
            return peer

        def heartbeat(self, peer):
            return peer

        def leave(self, peer_id):
            return peer_id

        def list_peers(self):
            return [
                {
                    "id": "peer-b",
                    "display_name": "Peer B",
                    "backend_port": 5102,
                    "status": "online",
                    "capabilities": {"device": "cpu"},
                    "last_seen_at": "2026-03-24T00:00:00+00:00",
                }
            ]

        def publish_event(self, event):
            return event

        def list_events(self, after=0, limit=None):
            return []

    settings = SimpleNamespace(
        instance_id="peer-a",
        instance_name="Peer A",
        backend_port=5101,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family="qwen",
        device="cpu",
        transport="localhost-coordinator",
        coordinator_base_url="http://localhost:9000",
    )
    adapter = MockP2PAdapter(settings, transport=FakeTransport())

    with Session(engine) as session:
        adapter.upsert_self(session)
        adapter.list_peers(session)
        remote = session.get(Peer, "peer-b")
        assert remote is not None

        remote.last_seen_at = remote.last_seen_at.replace(year=2020)
        session.add(remote)
        session.commit()

        changed = adapter.sweep_stale_peers(session)
        session.refresh(remote)

        assert changed == []
        assert remote.status == "online"


def test_coordinator_client_ignores_proxy_env_and_persists_events(monkeypatch):
    port = _start_real_coordinator()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("NO_PROXY", "")

    client = CoordinatorClient(f"http://127.0.0.1:{port}")
    client.register_peer(
        {
            "id": "peer-a",
            "display_name": "Peer A",
            "backend_port": 5101,
            "status": "online",
            "capabilities": {"device": "cpu"},
        }
    )
    client.publish_event(
        {
            "type": "message.created",
            "entity_id": "msg-1",
            "conversation_id": "conv-1",
            "payload": {"message": {"id": "msg-1", "content": "hello"}},
        }
    )

    events = client.list_events()
    assert any(event["type"] == "message.created" for event in events)


def test_two_app_instances_sync_messages_via_real_http_coordinator(tmp_path):
    coordinator_port = _start_real_coordinator()

    from fastapi.testclient import TestClient

    from paramind.apps.desktop.python.backend import create_app

    app_a = create_app(
        test_mode=True,
        settings_overrides={
            "app_data_dir": str(tmp_path),
            "instance_data_dir": str(tmp_path / "peer-a"),
            "instance_id": "peer-a",
            "instance_name": "Peer A",
            "backend_port": 5101,
            "coordinator_port": coordinator_port,
            "transport": "localhost-coordinator",
            "mock_inference_delay": 0.0,
        },
    )
    app_b = create_app(
        test_mode=True,
        settings_overrides={
            "app_data_dir": str(tmp_path),
            "instance_data_dir": str(tmp_path / "peer-b"),
            "instance_id": "peer-b",
            "instance_name": "Peer B",
            "backend_port": 5102,
            "coordinator_port": coordinator_port,
            "transport": "localhost-coordinator",
            "mock_inference_delay": 0.0,
        },
    )

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        general_id = client_a.get("/api/bootstrap").json()["conversations"][0]["id"]

        sent = client_a.post(
            f"/api/conversations/{general_id}/messages",
            json={"role": "user", "content": "hello over http coordinator"},
        )
        assert sent.status_code == 201

        deadline = time.time() + 2.0
        history = []
        while time.time() < deadline:
            history = client_b.get(f"/api/conversations/{general_id}/messages").json()
            if any(
                item["content"] == "hello over http coordinator" for item in history
            ):
                break
            time.sleep(0.05)

        mirrored = next(
            (
                item
                for item in history
                if item["content"] == "hello over http coordinator"
            ),
            None,
        )
        assert mirrored is not None
        assert mirrored["role"] == "peer"
        assert mirrored["sender_id"] == "peer-a"
