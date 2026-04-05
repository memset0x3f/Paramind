import json
import time
from pathlib import Path

import pytest

from paramind.apps.desktop.python.app.coordinator import CoordinatorState
from paramind.apps.desktop.python.backend import create_app


class _CoordinatorStateTransport:
    def __init__(self, state: CoordinatorState):
        self.state = state

    def register_peer(self, peer):
        return self.state.register_peer(peer)

    def heartbeat(self, peer):
        return self.state.heartbeat_peer(peer)

    def leave(self, peer_id):
        return self.state.leave_peer(peer_id)

    def list_peers(self):
        return self.state.list_peers()

    def publish_event(self, event):
        return self.state.publish_event(
            event_type=event["type"],
            entity_id=event["entity_id"],
            payload=dict(event.get("payload") or {}),
            conversation_id=event.get("conversation_id"),
        )

    def list_events(self, after=0, limit=None):
        return self.state.list_events(after=after, limit=limit)


def _make_client(
    tmp_path: Path,
    instance_id: str,
    instance_name: str,
    port: int,
    settings_overrides: dict | None = None,
):
    from fastapi.testclient import TestClient

    overrides = {
        "app_data_dir": str(tmp_path),
        "instance_data_dir": str(tmp_path / instance_id),
        "instance_id": instance_id,
        "instance_name": instance_name,
        "backend_port": port,
        "mock_inference_delay": 0.0,
    }
    if settings_overrides:
        overrides.update(settings_overrides)

    app = create_app(
        test_mode=True,
        settings_overrides=overrides,
    )
    return TestClient(app)


def _event_types(raw_sse: str):
    events = []
    for chunk in raw_sse.strip().split("\n\n"):
        event_type = None
        payload = None
        for line in chunk.splitlines():
            if line.startswith("event: "):
                event_type = line.removeprefix("event: ").strip()
            if line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: ").strip())
        if event_type and payload:
            events.append((event_type, payload))
    return events


def test_bootstrap_returns_self_peer_and_seeded_group_conversation(tmp_path):
    client = _make_client(tmp_path, "peer-a", "Peer A", 5101)

    response = client.get("/api/bootstrap")

    assert response.status_code == 200
    data = response.json()
    assert data["self"]["id"] == "peer-a"
    assert data["self"]["display_name"] == "Peer A"
    assert data["network"]["transport"] == "mock-local"
    assert isinstance(data["latest_event_id"], int)
    assert any(conv["kind"] == "group" for conv in data["conversations"])
    assert any(peer["id"] == "peer-a" for peer in data["peers"])


def test_bootstrap_exposes_latest_event_cursor_for_stream_resume(tmp_path):
    client = _make_client(tmp_path, "peer-a", "Peer A", 5101)
    before = client.get("/api/bootstrap").json()

    conversation_id = before["conversations"][0]["id"]
    sent = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "cursor test"},
    )
    assert sent.status_code == 201

    after = client.get("/api/bootstrap").json()
    assert after["latest_event_id"] >= before["latest_event_id"]
    assert after["latest_event_id"] > 0


def test_can_create_conversation_send_message_and_read_history(tmp_path):
    client = _make_client(tmp_path, "peer-a", "Peer A", 5101)

    create_response = client.post(
        "/api/conversations",
        json={"title": "Project Room", "kind": "group"},
    )
    assert create_response.status_code == 201
    conversation = create_response.json()

    message_response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"role": "user", "content": "Hello team"},
    )
    assert message_response.status_code == 201
    message = message_response.json()
    assert message["status"] == "sent"
    assert message["role"] == "user"

    history_response = client.get(f"/api/conversations/{conversation['id']}/messages")
    assert history_response.status_code == 200
    history = history_response.json()
    assert any(item["content"] == "Hello team" for item in history)


def test_inference_job_creates_assistant_message_and_stream_events(tmp_path):
    client = _make_client(tmp_path, "peer-a", "Peer A", 5101)
    bootstrap = client.get("/api/bootstrap").json()
    conversation_id = bootstrap["conversations"][0]["id"]

    user_message = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "Say hello in two words"},
    ).json()

    job_response = client.post(
        "/api/inference/jobs",
        json={
            "conversation_id": conversation_id,
            "user_message_id": user_message["id"],
        },
    )
    assert job_response.status_code == 202
    job = job_response.json()
    assert job["status"] in {"queued", "running", "streaming", "completed"}

    deadline = time.time() + 2.0
    messages = []
    while time.time() < deadline:
        messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
        assistant = next(
            (item for item in messages if item["id"] == job["assistant_message_id"]),
            None,
        )
        if assistant and assistant["status"] == "completed":
            break
        time.sleep(0.05)

    assistant = next(
        (item for item in messages if item["id"] == job["assistant_message_id"]), None
    )
    assert assistant is not None
    assert assistant["role"] == "assistant"
    assert assistant["status"] == "completed"
    assert assistant["content"] != ""

    stream_response = client.get(
        f"/api/conversations/{conversation_id}/stream",
        params={"after": 0, "limit": 32},
    )
    assert stream_response.status_code == 200
    assert "text/event-stream" in stream_response.headers["content-type"]

    events = _event_types(stream_response.text)
    event_names = [event_type for event_type, _ in events]
    assert "message.created" in event_names
    assert "job.started" in event_names
    assert "job.route" in event_names
    assert "message.token" in event_names
    assert "job.completed" in event_names


def test_second_instance_is_discoverable_as_peer(tmp_path):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    assert client_a.get("/api/bootstrap").status_code == 200
    assert client_b.get("/api/bootstrap").status_code == 200

    peers_response = client_a.get("/api/network/peers")
    assert peers_response.status_code == 200
    peers = peers_response.json()

    peer_ids = {peer["id"] for peer in peers}
    assert {"peer-a", "peer-b"} <= peer_ids


def test_message_from_one_instance_is_visible_to_another_via_shared_coordinator_state(
    tmp_path,
):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    general_id = client_a.get("/api/bootstrap").json()["conversations"][0]["id"]
    sent = client_a.post(
        f"/api/conversations/{general_id}/messages",
        json={"role": "user", "content": "hello from peer A"},
    )
    assert sent.status_code == 201

    deadline = time.time() + 2.0
    history = []
    while time.time() < deadline:
        history = client_b.get(f"/api/conversations/{general_id}/messages").json()
        if any(item["content"] == "hello from peer A" for item in history):
            break
        time.sleep(0.05)

    mirrored = next(
        (item for item in history if item["content"] == "hello from peer A"), None
    )
    assert mirrored is not None
    assert mirrored["role"] == "peer"
    assert mirrored["sender_id"] == "peer-a"


def test_dm_creation_is_idempotent_for_same_peer_pair(tmp_path):
    client = _make_client(tmp_path, "peer-a", "Peer A", 5101)

    first = client.post(
        "/api/conversations",
        json={"title": "Peer B", "kind": "dm", "participant_ids": ["peer-b"]},
    )
    second = client.post(
        "/api/conversations",
        json={"title": "Peer B", "kind": "dm", "participant_ids": ["peer-b"]},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"] == "dm:peer-a-peer-b"


def test_global_events_stream_pulls_remote_transport_events_without_bootstrap_polling(
    tmp_path,
):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    created = client_a.post("/api/dm/requests", json={"target_peer_id": "peer-b"})
    assert created.status_code == 201

    deadline = time.time() + 1.0
    events = []
    while time.time() < deadline:
        stream_response = client_b.get(
            "/api/events/stream", params={"after": 0, "limit": 128}
        )
        assert stream_response.status_code == 200
        events = _event_types(stream_response.text)
        if any(event_type == "dm.requested" for event_type, _ in events):
            break
        time.sleep(0.05)

    assert any(event_type == "dm.requested" for event_type, _ in events)


def test_conversation_stream_excludes_shell_only_lifecycle_events(tmp_path):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    general_id = client_b.get("/api/bootstrap").json()["conversations"][0]["id"]
    created = client_a.post("/api/dm/requests", json={"target_peer_id": "peer-b"})
    assert created.status_code == 201

    deadline = time.time() + 1.0
    events = []
    while time.time() < deadline:
        stream_response = client_b.get(
            f"/api/conversations/{general_id}/stream", params={"after": 0, "limit": 128}
        )
        assert stream_response.status_code == 200
        events = _event_types(stream_response.text)
        if events:
            break
        time.sleep(0.05)

    assert all(event_type != "dm.requested" for event_type, _ in events)


def test_dm_request_accept_flow_creates_single_dm_conversation(tmp_path):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    created = client_a.post("/api/dm/requests", json={"target_peer_id": "peer-b"})
    assert created.status_code == 201
    request_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    inbox = client_b.get("/api/dm/requests")
    assert inbox.status_code == 200
    assert any(
        item["id"] == request_id and item["direction"] == "inbound"
        for item in inbox.json()
    )

    deadline = time.time() + 2.0
    inbox = []
    while time.time() < deadline:
        inbox = client_b.get("/api/dm/requests").json()
        if any(item["id"] == request_id for item in inbox):
            break
        time.sleep(0.05)

    accepted = client_b.post(f"/api/dm/requests/{request_id}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["conversation_id"] == "dm:peer-a-peer-b"

    bootstrap_a = client_a.get("/api/bootstrap").json()
    bootstrap_b = client_b.get("/api/bootstrap").json()
    dm_a = next(
        (
            item
            for item in bootstrap_a["conversations"]
            if item["id"] == "dm:peer-a-peer-b"
        ),
        None,
    )
    dm_b = next(
        (
            item
            for item in bootstrap_b["conversations"]
            if item["id"] == "dm:peer-a-peer-b"
        ),
        None,
    )
    assert dm_a is not None
    assert dm_b is not None
    assert sorted(dm_a["participant_ids"]) == ["peer-a", "peer-b"]
    assert sorted(dm_b["participant_ids"]) == ["peer-a", "peer-b"]

    stream_response = client_a.get(
        "/api/events/stream",
        params={"after": 0, "limit": 128},
    )
    assert stream_response.status_code == 200
    events = _event_types(stream_response.text)
    accepted = next(
        (payload for event_type, payload in events if event_type == "dm.accepted"), None
    )
    assert accepted is not None
    assert accepted["payload"]["request"]["id"] == request_id
    assert accepted["payload"]["conversation"]["id"] == "dm:peer-a-peer-b"

    conversation_stream = client_a.get(
        f"/api/conversations/{bootstrap_a['conversations'][0]['id']}/stream",
        params={"after": 0, "limit": 128},
    )
    assert conversation_stream.status_code == 200
    conversation_event_names = [
        event_type for event_type, _ in _event_types(conversation_stream.text)
    ]
    assert "dm.accepted" not in conversation_event_names


def test_dm_request_is_removed_after_accept_or_reject(tmp_path):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    created = client_a.post("/api/dm/requests", json={"target_peer_id": "peer-b"})
    assert created.status_code == 201
    request_id = created.json()["id"]

    deadline = time.time() + 2.0
    inbox = []
    while time.time() < deadline:
        inbox = client_b.get("/api/dm/requests").json()
        if any(item["id"] == request_id for item in inbox):
            break
        time.sleep(0.05)

    accepted = client_b.post(f"/api/dm/requests/{request_id}/accept")
    assert accepted.status_code == 200

    requests_a = client_a.get("/api/dm/requests").json()
    requests_b = client_b.get("/api/dm/requests").json()
    assert all(item["id"] != request_id for item in requests_a)
    assert all(item["id"] != request_id for item in requests_b)

    bootstrap_a = client_a.get("/api/bootstrap").json()
    bootstrap_b = client_b.get("/api/bootstrap").json()
    assert all(item["id"] != request_id for item in bootstrap_a["dm_requests"])
    assert all(item["id"] != request_id for item in bootstrap_b["dm_requests"])

    coordinator_state_2 = CoordinatorState()
    shared_transport_2 = _CoordinatorStateTransport(coordinator_state_2)
    client_c = _make_client(
        tmp_path / "reject-case",
        "peer-c",
        "Peer C",
        5103,
        settings_overrides={"p2p_transport": shared_transport_2},
    )
    client_d = _make_client(
        tmp_path / "reject-case",
        "peer-d",
        "Peer D",
        5104,
        settings_overrides={"p2p_transport": shared_transport_2},
    )

    created_again = client_c.post("/api/dm/requests", json={"target_peer_id": "peer-d"})
    assert created_again.status_code == 201
    request_id_2 = created_again.json()["id"]

    deadline = time.time() + 2.0
    inbox = []
    while time.time() < deadline:
        inbox = client_d.get("/api/dm/requests").json()
        if any(item["id"] == request_id_2 for item in inbox):
            break
        time.sleep(0.05)

    rejected = client_d.post(f"/api/dm/requests/{request_id_2}/reject")
    assert rejected.status_code == 200

    requests_c = client_c.get("/api/dm/requests").json()
    requests_d = client_d.get("/api/dm/requests").json()
    assert all(item["id"] != request_id_2 for item in requests_c)
    assert all(item["id"] != request_id_2 for item in requests_d)


def test_bootstrap_includes_peer_relationships_and_group_invitations(tmp_path):
    client = _make_client(tmp_path, "peer-a", "Peer A", 5101)

    bootstrap = client.get("/api/bootstrap")

    assert bootstrap.status_code == 200
    data = bootstrap.json()
    assert "relationships" in data
    assert "group_invitations" in data
    assert isinstance(data["relationships"], list)
    assert isinstance(data["group_invitations"], list)


def test_group_invitation_accept_flow_creates_group_for_accepting_peer_only(tmp_path):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_c = _make_client(
        tmp_path,
        "peer-c",
        "Peer C",
        5103,
        settings_overrides={"p2p_transport": shared_transport},
    )

    created = client_a.post(
        "/api/group/invitations",
        json={"title": "Project Alpha", "target_peer_ids": ["peer-b", "peer-c"]},
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["conversation"]["kind"] == "group"
    assert len(payload["invitations"]) == 2

    deadline = time.time() + 2.0
    invites_b = []
    invites_c = []
    while time.time() < deadline:
        invites_b = client_b.get("/api/group/invitations").json()
        invites_c = client_c.get("/api/group/invitations").json()
        if invites_b and invites_c:
            break
        time.sleep(0.05)

    invite_b = invites_b[0]
    invite_c = invites_c[0]
    accepted = client_b.post(f"/api/group/invitations/{invite_b['id']}/accept")
    rejected = client_c.post(f"/api/group/invitations/{invite_c['id']}/reject")
    assert accepted.status_code == 200
    assert rejected.status_code == 200

    bootstrap_b = client_b.get("/api/bootstrap").json()
    bootstrap_c = client_c.get("/api/bootstrap").json()
    bootstrap_a = client_a.get("/api/bootstrap").json()

    group_id = payload["conversation"]["id"]
    group_a = next(
        (item for item in bootstrap_a["conversations"] if item["id"] == group_id), None
    )
    group_b = next(
        (item for item in bootstrap_b["conversations"] if item["id"] == group_id), None
    )
    assert group_a is not None
    assert group_b is not None
    assert all(item["id"] != group_id for item in bootstrap_c["conversations"])
    assert sorted(group_a["participant_ids"]) == ["peer-a", "peer-b"]
    assert sorted(group_b["participant_ids"]) == ["peer-a", "peer-b"]

    global_events = client_a.get(
        "/api/events/stream", params={"after": 0, "limit": 256}
    )
    assert global_events.status_code == 200
    event_names = [event_type for event_type, _ in _event_types(global_events.text)]
    assert "group.accepted" in event_names
    assert "group.rejected" in event_names


def test_accepting_group_invitation_returns_full_group_history(tmp_path):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    created = client_a.post(
        "/api/group/invitations",
        json={"title": "Project Alpha", "target_peer_ids": ["peer-b"]},
    )
    assert created.status_code == 201
    group_id = created.json()["conversation"]["id"]

    first = client_a.post(
        f"/api/conversations/{group_id}/messages",
        json={"role": "user", "content": "message before join 1"},
    )
    second = client_a.post(
        f"/api/conversations/{group_id}/messages",
        json={"role": "user", "content": "message before join 2"},
    )
    assert first.status_code == 201
    assert second.status_code == 201

    invites_b = client_b.get("/api/group/invitations").json()
    accepted = client_b.post(f"/api/group/invitations/{invites_b[0]['id']}/accept")
    assert accepted.status_code == 200
    payload = accepted.json()

    assert payload["conversation"]["id"] == group_id
    assert [item["content"] for item in payload["messages"]] == [
        "message before join 1",
        "message before join 2",
        "Peer B joined the group",
    ]
    assert payload["messages"][-1]["role"] == "system"
    assert payload["latest_conversation_event_id"] > 0

    history_b = client_b.get(f"/api/conversations/{group_id}/messages")
    assert history_b.status_code == 200
    assert [item["content"] for item in history_b.json()] == [
        "message before join 1",
        "message before join 2",
        "Peer B joined the group",
    ]


def test_group_invitation_events_are_visible_only_to_inviter_and_target_and_reject_keeps_group_hidden(
    tmp_path,
):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_c = _make_client(
        tmp_path,
        "peer-c",
        "Peer C",
        5103,
        settings_overrides={"p2p_transport": shared_transport},
    )

    created = client_a.post(
        "/api/group/invitations",
        json={"title": "Project Alpha", "target_peer_ids": ["peer-b"]},
    )
    assert created.status_code == 201
    group_id = created.json()["conversation"]["id"]

    invite_b = client_b.get("/api/group/invitations").json()[0]
    accepted_b = client_b.post(f"/api/group/invitations/{invite_b['id']}/accept")
    assert accepted_b.status_code == 200

    cursor_a = client_a.get("/api/bootstrap").json()["latest_global_event_id"]
    cursor_b = client_b.get("/api/bootstrap").json()["latest_global_event_id"]
    cursor_c = client_c.get("/api/bootstrap").json()["latest_global_event_id"]

    invited = client_a.post(
        "/api/group/invitations",
        json={"conversation_id": group_id, "target_peer_ids": ["peer-c"]},
    )
    assert invited.status_code == 201
    invitation_id = invited.json()["invitations"][0]["id"]

    invites_a = client_a.get("/api/group/invitations").json()
    invites_b = client_b.get("/api/group/invitations").json()
    invites_c = client_c.get("/api/group/invitations").json()

    assert [item["id"] for item in invites_a] == [invitation_id]
    assert invites_b == []
    assert [item["id"] for item in invites_c] == [invitation_id]

    events_a = _event_types(
        client_a.get(
            "/api/events/stream", params={"after": cursor_a, "limit": 128}
        ).text
    )
    events_b = _event_types(
        client_b.get(
            "/api/events/stream", params={"after": cursor_b, "limit": 128}
        ).text
    )
    events_c = _event_types(
        client_c.get(
            "/api/events/stream", params={"after": cursor_c, "limit": 128}
        ).text
    )

    assert "group.invited" in [event_type for event_type, _ in events_a]
    assert "group.invited" not in [event_type for event_type, _ in events_b]
    assert "group.invited" in [event_type for event_type, _ in events_c]

    rejected = client_c.post(f"/api/group/invitations/{invitation_id}/reject")
    assert rejected.status_code == 200

    bootstrap_c = client_c.get("/api/bootstrap").json()
    assert bootstrap_c["group_invitations"] == []
    assert all(item["id"] != group_id for item in bootstrap_c["conversations"])


def test_existing_group_member_can_invite_new_peer_and_all_members_see_updated_participants(
    tmp_path,
):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_c = _make_client(
        tmp_path,
        "peer-c",
        "Peer C",
        5103,
        settings_overrides={"p2p_transport": shared_transport},
    )

    created = client_a.post(
        "/api/group/invitations",
        json={"title": "Project Alpha", "target_peer_ids": ["peer-b"]},
    )
    assert created.status_code == 201
    group_id = created.json()["conversation"]["id"]

    invite_b = client_b.get("/api/group/invitations").json()[0]
    accepted_b = client_b.post(f"/api/group/invitations/{invite_b['id']}/accept")
    assert accepted_b.status_code == 200

    created_by_b = client_b.post(
        "/api/group/invitations",
        json={"conversation_id": group_id, "target_peer_ids": ["peer-c"]},
    )
    assert created_by_b.status_code == 201

    invite_c = client_c.get("/api/group/invitations").json()[0]
    accepted_c = client_c.post(f"/api/group/invitations/{invite_c['id']}/accept")
    assert accepted_c.status_code == 200

    bootstrap_a = client_a.get("/api/bootstrap").json()
    bootstrap_b = client_b.get("/api/bootstrap").json()
    bootstrap_c = client_c.get("/api/bootstrap").json()

    group_a = next(
        item for item in bootstrap_a["conversations"] if item["id"] == group_id
    )
    group_b = next(
        item for item in bootstrap_b["conversations"] if item["id"] == group_id
    )
    group_c = next(
        item for item in bootstrap_c["conversations"] if item["id"] == group_id
    )

    assert sorted(group_a["participant_ids"]) == ["peer-a", "peer-b", "peer-c"]
    assert sorted(group_b["participant_ids"]) == ["peer-a", "peer-b", "peer-c"]
    assert sorted(group_c["participant_ids"]) == ["peer-a", "peer-b", "peer-c"]


def test_leaving_group_updates_remaining_members_and_removes_group_for_leaver(tmp_path):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_c = _make_client(
        tmp_path,
        "peer-c",
        "Peer C",
        5103,
        settings_overrides={"p2p_transport": shared_transport},
    )

    created = client_a.post(
        "/api/group/invitations",
        json={"title": "Project Alpha", "target_peer_ids": ["peer-b", "peer-c"]},
    )
    assert created.status_code == 201
    group_id = created.json()["conversation"]["id"]

    invite_b = client_b.get("/api/group/invitations").json()[0]
    invite_c = client_c.get("/api/group/invitations").json()[0]
    assert (
        client_b.post(f"/api/group/invitations/{invite_b['id']}/accept").status_code
        == 200
    )
    assert (
        client_c.post(f"/api/group/invitations/{invite_c['id']}/accept").status_code
        == 200
    )

    left = client_b.post(f"/api/conversations/{group_id}/close")
    assert left.status_code == 200

    bootstrap_a = client_a.get("/api/bootstrap").json()
    bootstrap_b = client_b.get("/api/bootstrap").json()
    bootstrap_c = client_c.get("/api/bootstrap").json()

    group_a = next(
        item for item in bootstrap_a["conversations"] if item["id"] == group_id
    )
    group_c = next(
        item for item in bootstrap_c["conversations"] if item["id"] == group_id
    )
    assert sorted(group_a["participant_ids"]) == ["peer-a", "peer-c"]
    assert sorted(group_c["participant_ids"]) == ["peer-a", "peer-c"]
    assert all(item["id"] != group_id for item in bootstrap_b["conversations"])


def test_ai_messages_do_not_add_virtual_participants_to_group_summary(tmp_path):
    client = _make_client(tmp_path, "peer-a", "Peer A", 5101)

    bootstrap = client.get("/api/bootstrap").json()
    conversation_id = bootstrap["conversations"][0]["id"]
    before = next(
        item for item in bootstrap["conversations"] if item["id"] == conversation_id
    )
    assert "assistant" not in before["participant_ids"]

    requested = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "@AI 介绍一下你自己"},
    )
    assert requested.status_code == 201

    draft = client.post(
        "/api/ai/drafts",
        json={
            "conversation_id": conversation_id,
            "source_message_id": requested.json()["id"],
        },
    )
    assert draft.status_code == 201

    deadline = time.time() + 2.0
    while time.time() < deadline:
        history = client.get(f"/api/conversations/{conversation_id}/messages").json()
        local_draft = next(
            (item for item in history if item["id"] == draft.json()["draft"]["id"]),
            None,
        )
        if local_draft and local_draft["status"] == "completed":
            break
        time.sleep(0.05)

    published = client.post(f"/api/ai/drafts/{draft.json()['draft']['id']}/send")
    assert published.status_code == 201

    refreshed = client.get("/api/bootstrap").json()
    after = next(
        item for item in refreshed["conversations"] if item["id"] == conversation_id
    )
    assert "assistant" not in after["participant_ids"]
    assert "ai" not in [item.lower() for item in after["participant_ids"]]
    assert sorted(after["participant_ids"]) == sorted(before["participant_ids"])


def test_ai_draft_lifecycle_uses_explicit_user_ai_request_and_publish_turns_into_ai_message(
    tmp_path,
):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    conversation_id = client_a.get("/api/bootstrap").json()["conversations"][0]["id"]
    requested = client_a.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "@AI 介绍一下你自己"},
    )
    assert requested.status_code == 201
    request_message = requested.json()

    created = client_a.post(
        "/api/ai/drafts",
        json={
            "conversation_id": conversation_id,
            "source_message_id": request_message["id"],
        },
    )
    assert created.status_code == 201
    draft = created.json()["draft"]
    draft_id = draft["id"]

    deadline = time.time() + 2.0
    draft_history = []
    while time.time() < deadline:
        draft_history = client_a.get(
            f"/api/conversations/{conversation_id}/messages"
        ).json()
        local_draft = next(
            (item for item in draft_history if item["id"] == draft_id), None
        )
        if local_draft and local_draft["status"] == "completed":
            break
        time.sleep(0.05)

    local_draft = next((item for item in draft_history if item["id"] == draft_id), None)
    assert local_draft is not None
    assert local_draft["metadata"]["local_draft"] is True
    bootstrap_after_draft = client_a.get("/api/bootstrap").json()
    general_after_draft = next(
        item
        for item in bootstrap_after_draft["conversations"]
        if item["id"] == conversation_id
    )
    assert general_after_draft["last_message"]["id"] == request_message["id"]

    remote_history = client_b.get(
        f"/api/conversations/{conversation_id}/messages"
    ).json()
    mirrored_request = next(
        (item for item in remote_history if item["id"] == request_message["id"]), None
    )
    assert mirrored_request is not None
    assert mirrored_request["role"] == "peer"
    assert mirrored_request["content"] == "@AI 介绍一下你自己"

    assert all(item["id"] != draft_id for item in remote_history)

    updated = client_a.patch(
        f"/api/ai/drafts/{draft_id}", json={"content": "这是经过编辑的草稿"}
    )
    assert updated.status_code == 200
    assert updated.json()["content"] == "这是经过编辑的草稿"

    sent = client_a.post(f"/api/ai/drafts/{draft_id}/send")
    assert sent.status_code == 201
    assert sent.json()["role"] == "assistant"
    assert sent.json()["sender_name"] == "AI"
    assert sent.json()["content"] == "这是经过编辑的草稿"

    deadline = time.time() + 2.0
    mirrored = None
    while time.time() < deadline:
        remote_history = client_b.get(
            f"/api/conversations/{conversation_id}/messages"
        ).json()
        mirrored = next(
            (
                item
                for item in remote_history
                if item["content"] == "这是经过编辑的草稿"
            ),
            None,
        )
        if mirrored is not None:
            break
        time.sleep(0.05)

    assert mirrored is not None
    assert mirrored["role"] == "assistant"
    assert mirrored["sender_name"] == "AI"

    local_after_send = client_a.get(
        f"/api/conversations/{conversation_id}/messages"
    ).json()
    assert all(item["id"] != draft_id for item in local_after_send)


def test_published_ai_message_syncs_as_formal_assistant_message_without_leaking_local_draft(
    tmp_path,
):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    conversation_id = client_a.get("/api/bootstrap").json()["conversations"][0]["id"]
    requested = client_a.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "@AI 用一句话介绍你自己"},
    )
    assert requested.status_code == 201
    request_message = requested.json()

    created = client_a.post(
        "/api/ai/drafts",
        json={
            "conversation_id": conversation_id,
            "source_message_id": request_message["id"],
        },
    )
    assert created.status_code == 201
    draft_id = created.json()["draft"]["id"]

    deadline = time.time() + 2.0
    while time.time() < deadline:
        local_history = client_a.get(
            f"/api/conversations/{conversation_id}/messages"
        ).json()
        local_draft = next(
            (item for item in local_history if item["id"] == draft_id), None
        )
        if local_draft and local_draft["status"] == "completed":
            break
        time.sleep(0.05)

    published = client_a.post(f"/api/ai/drafts/{draft_id}/send")
    assert published.status_code == 201
    assert published.json()["role"] == "assistant"
    assert published.json()["sender_name"] == "AI"
    assert published.json()["metadata"]["published_from_draft"] is True

    deadline = time.time() + 2.0
    remote_published = None
    remote_history = []
    while time.time() < deadline:
        remote_history = client_b.get(
            f"/api/conversations/{conversation_id}/messages"
        ).json()
        remote_published = next(
            (item for item in remote_history if item["id"] == published.json()["id"]),
            None,
        )
        if remote_published is not None:
            break
        time.sleep(0.05)

    assert remote_published is not None
    assert remote_published["role"] == "assistant"
    assert remote_published["sender_name"] == "AI"
    assert remote_published["metadata"]["published_from_draft"] is True
    assert all(item["id"] != draft_id for item in remote_history)


def test_remote_message_sync_preserves_source_timestamps(tmp_path):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    conversation_id = client_a.get("/api/bootstrap").json()["conversations"][0]["id"]
    sent = client_a.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "timestamp sync"},
    )
    assert sent.status_code == 201
    local_message = sent.json()

    deadline = time.time() + 2.0
    remote_message = None
    while time.time() < deadline:
        remote_history = client_b.get(
            f"/api/conversations/{conversation_id}/messages"
        ).json()
        remote_message = next(
            (item for item in remote_history if item["id"] == local_message["id"]), None
        )
        if remote_message is not None:
            break
        time.sleep(0.05)

    assert remote_message is not None
    assert remote_message["created_at"] == local_message["created_at"]
    assert remote_message["updated_at"] == local_message["updated_at"]

    bootstrap_a = client_a.get("/api/bootstrap").json()
    bootstrap_b = client_b.get("/api/bootstrap").json()
    local_conversation = next(
        item for item in bootstrap_a["conversations"] if item["id"] == conversation_id
    )
    remote_conversation = next(
        item for item in bootstrap_b["conversations"] if item["id"] == conversation_id
    )
    assert remote_conversation["updated_at"] == local_conversation["updated_at"]


def test_message_ack_and_sync_events_expose_read_status(tmp_path):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    conversation_id = client_a.get("/api/bootstrap").json()["conversations"][0]["id"]
    sent = client_a.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "read me"},
    )
    assert sent.status_code == 201
    message_id = sent.json()["id"]

    deadline = time.time() + 2.0
    mirrored = None
    while time.time() < deadline:
        remote_history = client_b.get(
            f"/api/conversations/{conversation_id}/messages"
        ).json()
        mirrored = next(
            (item for item in remote_history if item["id"] == message_id), None
        )
        if mirrored is not None:
            break
        time.sleep(0.05)

    assert mirrored is not None

    ack = client_b.post(f"/api/messages/{message_id}/ack", json={"status": "read"})
    assert ack.status_code == 200
    assert ack.json()["status"] == "read"

    deadline = time.time() + 2.0
    local = None
    while time.time() < deadline:
        history = client_a.get(f"/api/conversations/{conversation_id}/messages").json()
        local = next((item for item in history if item["id"] == message_id), None)
        if local and local["status"] == "read":
            break
        time.sleep(0.05)

    assert local is not None
    assert local["status"] == "read"

    events = client_a.get("/api/sync/events", params={"after": 0, "limit": 64})
    assert events.status_code == 200
    assert any(item["type"] == "message.ack" for item in events.json()["events"])


def test_leaving_dm_removes_conversation_for_both_peers(tmp_path):
    coordinator_state = CoordinatorState()
    shared_transport = _CoordinatorStateTransport(coordinator_state)
    client_a = _make_client(
        tmp_path,
        "peer-a",
        "Peer A",
        5101,
        settings_overrides={"p2p_transport": shared_transport},
    )
    client_b = _make_client(
        tmp_path,
        "peer-b",
        "Peer B",
        5102,
        settings_overrides={"p2p_transport": shared_transport},
    )

    created = client_a.post("/api/dm/requests", json={"target_peer_id": "peer-b"})
    request_id = created.json()["id"]
    deadline = time.time() + 2.0
    while time.time() < deadline:
        inbox = client_b.get("/api/dm/requests").json()
        if any(item["id"] == request_id for item in inbox):
            break
        time.sleep(0.05)
    accepted = client_b.post(f"/api/dm/requests/{request_id}/accept")
    assert accepted.status_code == 200
    conversation_id = accepted.json()["conversation_id"]

    left = client_a.post(f"/api/conversations/{conversation_id}/leave")
    assert left.status_code == 200

    deadline = time.time() + 2.0
    bootstrap_a = bootstrap_b = None
    while time.time() < deadline:
        bootstrap_a = client_a.get("/api/bootstrap").json()
        bootstrap_b = client_b.get("/api/bootstrap").json()
        ids_a = {item["id"] for item in bootstrap_a["conversations"]}
        ids_b = {item["id"] for item in bootstrap_b["conversations"]}
        if conversation_id not in ids_a and conversation_id not in ids_b:
            break
        time.sleep(0.05)

    assert conversation_id not in {item["id"] for item in bootstrap_a["conversations"]}
    assert conversation_id not in {item["id"] for item in bootstrap_b["conversations"]}
