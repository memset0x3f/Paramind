import json

from inference.NodeInventory import NodeState
from scripts import scan_local_hardware


def test_text_render_includes_key_metrics(monkeypatch):
    monkeypatch.setattr(
        scan_local_hardware,
        "from_local_snapshot",
        lambda node_id, host=None, kv_headroom_gb=0.0, loaded_ranges=None: NodeState(
            node_id=node_id,
            host=host or "demo.local",
            device_type="cuda",
            total_memory_gb=64.0,
            free_memory_gb=48.0,
            kv_headroom_gb=4.0,
            max_blocks_capacity=22,
            block_throughput=80.0,
            loaded_ranges=[(0, 12)],
        ),
    )

    rendered = scan_local_hardware.render_text(
        scan_local_hardware.scan_local_node("node-a")
    )

    assert "node_id=node-a" in rendered
    assert "device_type=cuda" in rendered
    assert "free_memory_gb=48.0" in rendered
    assert "effective_speed=" in rendered
    assert "effective_capacity_blocks=22" in rendered


def test_json_mode_returns_parseable_required_fields(monkeypatch):
    monkeypatch.setattr(
        scan_local_hardware,
        "from_local_snapshot",
        lambda node_id, host=None, kv_headroom_gb=0.0, loaded_ranges=None: NodeState(
            node_id=node_id,
            host=host or "demo.local",
            device_type="cpu",
            total_memory_gb=32.0,
            free_memory_gb=20.0,
            max_blocks_capacity=10,
            block_throughput=12.5,
            loaded_ranges=[],
        ),
    )

    payload = scan_local_hardware.render_json(
        scan_local_hardware.scan_local_node("node-b")
    )
    data = json.loads(payload)

    assert data["node_id"] == "node-b"
    assert data["device_type"] == "cpu"
    assert "effective_speed" in data
    assert "effective_capacity_blocks" in data
    assert "loaded_ranges" in data


def test_scan_uses_nodeinventory_snapshot_helper(monkeypatch):
    called = {}

    def fake_snapshot(node_id, host=None, kv_headroom_gb=0.0, loaded_ranges=None):
        called["node_id"] = node_id
        return NodeState(
            node_id=node_id,
            host=host or "demo.local",
            device_type="cpu",
            total_memory_gb=32.0,
            free_memory_gb=16.0,
            max_blocks_capacity=8,
            block_throughput=10.0,
            loaded_ranges=[],
        )

    monkeypatch.setattr(scan_local_hardware, "from_local_snapshot", fake_snapshot)

    state = scan_local_hardware.scan_local_node("node-c")

    assert called["node_id"] == "node-c"
    assert state.node_id == "node-c"
