from inference.DeviceProfile import build_node_profile
from inference.ClusterTypes import NodeProfile


def test_build_node_profile_returns_capacity_fields(monkeypatch):
    monkeypatch.setattr("inference.DeviceProfile._detect_device", lambda: "cpu")
    monkeypatch.setattr(
        "inference.DeviceProfile._detect_memory_gb", lambda: (16.0, 10.5)
    )

    profile = build_node_profile(
        node_id="node-a", host="10.0.0.1", loaded_shards=[(0, 12)]
    )

    assert profile.node_id == "node-a"
    assert profile.device == "cpu"
    assert profile.total_memory_gb == 16.0
    assert profile.free_memory_gb == 10.5
    assert profile.loaded_shards == [(0, 12)]


def test_node_profile_payload_round_trip(monkeypatch):
    monkeypatch.setattr("inference.DeviceProfile._detect_device", lambda: "cpu")
    monkeypatch.setattr(
        "inference.DeviceProfile._detect_memory_gb", lambda: (16.0, 10.5)
    )

    profile = build_node_profile(
        node_id="node-a", host="10.0.0.1", loaded_shards=[(0, 12)]
    )
    payload = profile.to_dict()
    restored = NodeProfile.from_dict(payload)

    assert payload["node_id"] == "node-a"
    assert payload["loaded_shards"] == [[0, 12]]
    assert restored == profile
