# Cluster Distribution and Stability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the real distributed control plane for ParaMind in three stages: static cluster distribution, smart re-planning with minimal movement, and node join/leave stability.

**Architecture:** Separate the system into a control plane and a data plane. The control plane discovers nodes, profiles devices, elects the strongest node as coordinator, computes shard placement, broadcasts assignments, waits for all nodes to load, and triggers inference. The data plane keeps using `ShardLoader` and `DistributedInferenceEngine`, but replaces single-process simulation with real remote shard ownership and RPC-style forwarding.

**Tech Stack:** Python, PyTorch, HuggingFace Transformers, current `p2p/P2PClient.py` transport, dataclasses, pytest, existing inference stack (`ShardConfig`, `ShardLoader`, `DistributedInferenceEngine`).

---

## Scope and non-goals

### In scope
- Profile local node capacity (device, memory, current shard ownership)
- Discover peers already on the network
- Elect a coordinator (strongest node for now)
- Compute a static shard assignment for all online nodes
- Broadcast assignments and wait for load readiness
- Run real remote shard forwarding with the existing P2P transport layer
- Add a smarter planner that minimizes shard movement across re-plans
- Add join/leave handling and safe re-plan barriers

### Out of scope for this plan
- Frontend route visualization polish
- Full fault-tolerant recovery after mid-token remote failure
- Auto-scaling policies based on live latency
- GPU telemetry perfection across all vendors

### Design constraints
- Reuse `inference/ShardLoader.py` and `inference/DistributedEngine.py`; do not reintroduce `ModelSplitter`.
- Keep the first real distributed version simple: one coordinator, one current plan, one inference at a time.
- Prefer new control-plane modules over making `p2p/P2PClient.py` hold planner logic.
- Treat stability as controlled reconfiguration, not magic seamless migration.

---

## Proposed file map

### New inference/control files
- Create: `inference/ClusterTypes.py`
- Create: `inference/DeviceProfile.py`
- Create: `inference/ClusterPlanner.py`
- Create: `inference/ClusterCoordinator.py`
- Create: `inference/NodeRuntime.py`

### Existing inference files to modify
- Modify: `inference/DistributedEngine.py`
- Modify: `inference/__init__.py`
- Modify: `inference/README.md`

### P2P files to modify
- Modify: `p2p/P2PClient.py`
- Modify: `p2p/__init__.py`

### Tests
- Create: `test/p2p/test_device_profile.py`
- Create: `test/p2p/test_static_cluster_planner.py`
- Create: `test/p2p/test_cluster_coordinator.py`
- Create: `test/p2p/test_remote_distributed_engine.py`
- Create: `test/p2p/test_sticky_replanner.py`
- Create: `test/p2p/test_cluster_stability.py`

### Docs
- Modify: `docs/README.md`
- Modify: `docs/diary/2026-03-24.md`

---

## Phase 1 — Static cluster distribution (real nodes, no smart rebalancing yet)

### Task 1: Introduce cluster control types

**Files:**
- Create: `inference/ClusterTypes.py`
- Test: `test/p2p/test_static_cluster_planner.py`

**Step 1: Write the failing test**

```python
from inference.ClusterTypes import NodeProfile, ShardAssignment, PlacementPlan


def test_placement_plan_preserves_layer_order():
    plan = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        assignments=[
            ShardAssignment(node_id="node-a", start_layer=0, end_layer=12),
            ShardAssignment(node_id="node-b", start_layer=12, end_layer=24),
        ],
    )

    assert [a.start_layer for a in plan.assignments] == [0, 12]
    assert [a.end_layer for a in plan.assignments] == [12, 24]
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_static_cluster_planner.py::test_placement_plan_preserves_layer_order
```

Expected: FAIL with `ModuleNotFoundError` for `inference.ClusterTypes`

**Step 3: Write minimal implementation**

```python
# inference/ClusterTypes.py
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class NodeProfile:
    node_id: str
    host: str
    device: str
    total_memory_gb: float
    free_memory_gb: float
    compute_score: float
    loaded_shards: List[tuple[int, int]] = field(default_factory=list)


@dataclass(frozen=True)
class ShardAssignment:
    node_id: str
    start_layer: int
    end_layer: int
    role: str = "middle"
    source_node_id: Optional[str] = None


@dataclass(frozen=True)
class PlacementPlan:
    model_id: str
    assignments: List[ShardAssignment]
    coordinator_id: Optional[str] = None
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_static_cluster_planner.py::test_placement_plan_preserves_layer_order
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterTypes.py test/p2p/test_static_cluster_planner.py
git commit -m "feat: add cluster placement types"
```

---

### Task 2: Add local device profiling

**Files:**
- Create: `inference/DeviceProfile.py`
- Modify: `inference/ClusterTypes.py`
- Test: `test/p2p/test_device_profile.py`

**Step 1: Write the failing test**

```python
from inference.DeviceProfile import build_node_profile


def test_build_node_profile_returns_capacity_fields(monkeypatch):
    monkeypatch.setattr("inference.DeviceProfile._detect_device", lambda: "cpu")
    monkeypatch.setattr("inference.DeviceProfile._detect_memory_gb", lambda: (16.0, 10.5))

    profile = build_node_profile(node_id="node-a", host="10.0.0.1", loaded_shards=[(0, 12)])

    assert profile.node_id == "node-a"
    assert profile.device == "cpu"
    assert profile.total_memory_gb == 16.0
    assert profile.free_memory_gb == 10.5
    assert profile.loaded_shards == [(0, 12)]
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_device_profile.py::test_build_node_profile_returns_capacity_fields
```

Expected: FAIL because `build_node_profile` does not exist

**Step 3: Write minimal implementation**

```python
# inference/DeviceProfile.py
import socket
import torch

from inference.ClusterTypes import NodeProfile


def _detect_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def _detect_memory_gb() -> tuple[float, float]:
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        total = props.total_memory / (1024 ** 3)
        reserved = torch.cuda.memory_reserved(0) / (1024 ** 3)
        return total, max(total - reserved, 0.0)
    return 0.0, 0.0


def _compute_score(device: str, total_gb: float, free_gb: float) -> float:
    base = free_gb + total_gb * 0.25
    return base + (1000.0 if device == "cuda" else 0.0)


def build_node_profile(node_id: str, host: str | None = None, loaded_shards=None) -> NodeProfile:
    device = _detect_device()
    total, free = _detect_memory_gb()
    host = host or socket.gethostbyname(socket.gethostname())
    loaded_shards = loaded_shards or []
    return NodeProfile(
        node_id=node_id,
        host=host,
        device=device,
        total_memory_gb=total,
        free_memory_gb=free,
        compute_score=_compute_score(device, total, free),
        loaded_shards=loaded_shards,
    )
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_device_profile.py::test_build_node_profile_returns_capacity_fields
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/DeviceProfile.py test/p2p/test_device_profile.py
git commit -m "feat: add node device profiling"
```

---

### Task 3: Build a static cluster planner

**Files:**
- Create: `inference/ClusterPlanner.py`
- Modify: `inference/ClusterTypes.py`
- Test: `test/p2p/test_static_cluster_planner.py`

**Step 1: Write the failing test**

```python
from inference.ClusterPlanner import plan_static_distribution
from inference.ClusterTypes import NodeProfile


def test_static_distribution_assigns_contiguous_ranges():
    nodes = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 28),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 20, 20),
        NodeProfile("node-c", "10.0.0.3", "cpu", 32, 18, 18),
    ]

    plan = plan_static_distribution(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
        nodes=nodes,
    )

    assert [(a.start_layer, a.end_layer) for a in plan.assignments] == [
        (0, 8), (8, 16), (16, 24)
    ]
    assert plan.coordinator_id == "node-a"
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_static_cluster_planner.py::test_static_distribution_assigns_contiguous_ranges
```

Expected: FAIL because planner is missing

**Step 3: Write minimal implementation**

```python
# inference/ClusterPlanner.py
from inference.ClusterTypes import PlacementPlan, ShardAssignment, NodeProfile


def choose_coordinator(nodes: list[NodeProfile]) -> str:
    return max(nodes, key=lambda n: n.compute_score).node_id


def plan_static_distribution(model_id: str, total_layers: int, nodes: list[NodeProfile]) -> PlacementPlan:
    ordered_nodes = sorted(nodes, key=lambda n: n.compute_score, reverse=True)
    base = total_layers // len(ordered_nodes)
    rem = total_layers % len(ordered_nodes)

    start = 0
    assignments = []
    for idx, node in enumerate(ordered_nodes):
        size = base + (1 if idx < rem else 0)
        end = start + size
        role = "first" if start == 0 else "last" if end == total_layers else "middle"
        assignments.append(ShardAssignment(node.node_id, start, end, role=role))
        start = end

    return PlacementPlan(
        model_id=model_id,
        assignments=assignments,
        coordinator_id=choose_coordinator(nodes),
    )
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_static_cluster_planner.py::test_static_distribution_assigns_contiguous_ranges
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterPlanner.py test/p2p/test_static_cluster_planner.py
git commit -m "feat: add static cluster distribution planner"
```

---

### Task 4: Add coordinator orchestration for snapshot → plan → broadcast → ready barrier

**Files:**
- Create: `inference/ClusterCoordinator.py`
- Modify: `inference/ClusterPlanner.py`
- Test: `test/p2p/test_cluster_coordinator.py`

**Step 1: Write the failing test**

```python
from inference.ClusterCoordinator import ClusterCoordinator
from inference.ClusterTypes import NodeProfile


class FakeTransport:
    def __init__(self):
        self.sent = []

    def broadcast(self, event, payload):
        self.sent.append((event, payload))


def test_coordinator_broadcasts_plan_after_collecting_profiles():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(transport=transport, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24)

    profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 28),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 24, 24),
    ]

    plan = coordinator.build_plan(profiles)
    coordinator.broadcast_plan(plan)

    assert transport.sent[0][0] == "cluster_plan"
    assert transport.sent[0][1]["coordinator_id"] == plan.coordinator_id
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_coordinator.py::test_coordinator_broadcasts_plan_after_collecting_profiles
```

Expected: FAIL because `ClusterCoordinator` does not exist

**Step 3: Write minimal implementation**

```python
# inference/ClusterCoordinator.py
from dataclasses import asdict

from inference.ClusterPlanner import plan_static_distribution


class ClusterCoordinator:
    def __init__(self, transport, model_id: str, total_layers: int):
        self.transport = transport
        self.model_id = model_id
        self.total_layers = total_layers

    def build_plan(self, profiles):
        return plan_static_distribution(self.model_id, self.total_layers, profiles)

    def broadcast_plan(self, plan):
        self.transport.broadcast("cluster_plan", {
            "model_id": plan.model_id,
            "coordinator_id": plan.coordinator_id,
            "assignments": [asdict(a) for a in plan.assignments],
        })
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_coordinator.py::test_coordinator_broadcasts_plan_after_collecting_profiles
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_cluster_coordinator.py
git commit -m "feat: add static cluster coordinator"
```

---

### Task 5: Add node runtime for receiving assignments and loading local shard

**Files:**
- Create: `inference/NodeRuntime.py`
- Modify: `inference/ShardConfig.py`
- Test: `test/p2p/test_cluster_coordinator.py`

**Step 1: Write the failing test**

```python
from inference.NodeRuntime import NodeRuntime
from inference.ClusterTypes import ShardAssignment
from inference.ShardConfig import ModelFamily


class FakeLoader:
    def __init__(self):
        self.loaded = None

    def load_assignment(self, assignment):
        self.loaded = assignment


def test_node_runtime_loads_only_its_assignment():
    loader = FakeLoader()
    runtime = NodeRuntime(node_id="node-b", family=ModelFamily.QWEN, total_layers=24, loader=loader)
    assignment = ShardAssignment(node_id="node-b", start_layer=12, end_layer=24, role="last")

    runtime.apply_assignment(model_id="Qwen/Qwen2.5-0.5B-Instruct", assignment=assignment)

    assert loader.loaded == assignment
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_coordinator.py::test_node_runtime_loads_only_its_assignment
```

Expected: FAIL because `NodeRuntime` does not exist

**Step 3: Write minimal implementation**

```python
# inference/NodeRuntime.py
from inference.ShardConfig import ShardConfig
from inference.ShardLoader import ShardLoader


class NodeRuntime:
    def __init__(self, node_id, family, total_layers, device="cpu", loader=None):
        self.node_id = node_id
        self.family = family
        self.total_layers = total_layers
        self.device = device
        self.loader = loader
        self.local_shard = None
        self.assignment = None

    def apply_assignment(self, model_id, assignment):
        self.assignment = assignment
        if self.loader is not None:
            self.loader.load_assignment(assignment)
            return

        cfg = ShardConfig(
            model_id=model_id,
            family=self.family,
            start_layer=assignment.start_layer,
            end_layer=assignment.end_layer,
            total_layers=self.total_layers,
            dtype="float16",
        )
        self.local_shard = ShardLoader(cfg, device=self.device).load()
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_coordinator.py::test_node_runtime_loads_only_its_assignment
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/NodeRuntime.py test/p2p/test_cluster_coordinator.py
git commit -m "feat: add node runtime for shard assignment"
```

---

### Task 6: Refactor `P2PClient` into transport instead of model owner

**Files:**
- Modify: `p2p/P2PClient.py`
- Modify: `p2p/__init__.py`
- Test: `test/p2p/test_remote_distributed_engine.py`

**Step 1: Write the failing test**

```python
from p2p import P2PClient


def test_p2p_client_can_register_message_handler_without_loading_model():
    client = P2PClient.__new__(P2PClient)
    client._handlers = {}
    client.register_handler("cluster_plan", lambda payload: payload)

    assert "cluster_plan" in client._handlers
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_remote_distributed_engine.py::test_p2p_client_can_register_message_handler_without_loading_model
```

Expected: FAIL because the current `P2PClient` is tightly coupled to `QwenSlice`

**Step 3: Write minimal implementation**

```python
# p2p/P2PClient.py (target shape)
class P2PClient:
    def __init__(self, signalServer: str, node_id: str):
        self.signalServerAddr = signalServer
        self.node_id = node_id
        self._handlers = {}
        ...

    def register_handler(self, event_type: str, handler):
        self._handlers[event_type] = handler

    def broadcast(self, event_type: str, payload: dict):
        ...

    def send_to_peer(self, peer_id: str, event_type: str, payload: dict):
        ...
```

Remove these responsibilities from `P2PClient`:
- local model loading
- tokenizer ownership
- decode loop ownership
- `QwenSlice` dependency

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_remote_distributed_engine.py::test_p2p_client_can_register_message_handler_without_loading_model
```

Expected: PASS

**Step 5: Commit**

```bash
git add p2p/P2PClient.py p2p/__init__.py test/p2p/test_remote_distributed_engine.py
git commit -m "refactor: decouple p2p client from local model ownership"
```

---

### Task 7: Replace simulation-only remote forwarding with real transport hooks

**Files:**
- Modify: `inference/DistributedEngine.py`
- Modify: `inference/NodeRuntime.py`
- Test: `test/p2p/test_remote_distributed_engine.py`

**Step 1: Write the failing test**

```python
from inference.DistributedEngine import DistributedInferenceEngine
from inference.ShardConfig import ModelFamily, ShardConfig


class FakeP2P:
    def __init__(self):
        self.calls = []

    def remote_forward(self, shard_index, tensor_payload, kv_payload):
        self.calls.append((shard_index, tensor_payload.shape))
        return tensor_payload, kv_payload


def test_distributed_engine_uses_remote_forward_for_non_local_shards():
    engine = DistributedInferenceEngine(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        family=ModelFamily.QWEN,
        shard_configs=[
            ShardConfig("Qwen/Qwen2.5-0.5B-Instruct", ModelFamily.QWEN, 0, 12, 24, "float16"),
            ShardConfig("Qwen/Qwen2.5-0.5B-Instruct", ModelFamily.QWEN, 12, 24, 24, "float16"),
        ],
        device="cpu",
        local_shard_index=0,
        p2p_client=FakeP2P(),
    )

    # call the hook directly to avoid full generation cost
    x = object()
    kv = object()
    engine._remote_forward(1, x, kv)

    assert len(engine.p2p_client.calls) == 1
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_remote_distributed_engine.py::test_distributed_engine_uses_remote_forward_for_non_local_shards
```

Expected: FAIL because `_remote_forward` raises `NotImplementedError`

**Step 3: Write minimal implementation**

```python
# inference/DistributedEngine.py
    def _remote_forward(self, shard_index, x, kv_cache):
        if self.p2p_client is None:
            raise RuntimeError(...)
        return self.p2p_client.remote_forward(
            shard_index=shard_index,
            tensor_payload=x,
            kv_payload=kv_cache,
        )
```

Add a matching method to the P2P layer / node runtime path that:
- sends hidden states to the owning node
- waits for that node's forward result
- returns the next hidden states or logits

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_remote_distributed_engine.py::test_distributed_engine_uses_remote_forward_for_non_local_shards
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/DistributedEngine.py inference/NodeRuntime.py p2p/P2PClient.py test/p2p/test_remote_distributed_engine.py
git commit -m "feat: add remote shard forwarding hook"
```

---

### Task 8: Add an end-to-end static cluster test (coordinator + 2 nodes)

**Files:**
- Modify: `test/p2p/test_remote_distributed_engine.py`
- Modify: `test/p2p/test_cluster_coordinator.py`

**Step 1: Write the failing test**

```python
def test_static_cluster_can_plan_load_and_mark_ready():
    # Build three fake node profiles
    # Coordinator computes plan
    # Each fake node runtime receives its assignment
    # Each runtime marks ready
    # Coordinator sees all nodes ready
    assert False
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_coordinator.py::test_static_cluster_can_plan_load_and_mark_ready
```

Expected: FAIL because readiness orchestration does not exist

**Step 3: Write minimal implementation**

Implement these coordinator methods:

```python
class ClusterCoordinator:
    def __init__(...):
        self.ready_nodes = set()

    def mark_ready(self, node_id: str):
        self.ready_nodes.add(node_id)

    def all_ready(self, plan) -> bool:
        expected = {assignment.node_id for assignment in plan.assignments}
        return expected.issubset(self.ready_nodes)
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_coordinator.py::test_static_cluster_can_plan_load_and_mark_ready
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_cluster_coordinator.py
git commit -m "feat: add cluster ready barrier"
```

---

## Phase 2 — Smart planning (minimize movement when topology changes)

### Task 9: Add sticky placement scoring

**Files:**
- Modify: `inference/ClusterPlanner.py`
- Test: `test/p2p/test_sticky_replanner.py`

**Step 1: Write the failing test**

```python
from inference.ClusterPlanner import score_assignment_move_cost


def test_move_cost_prefers_existing_shard_owner():
    cost_same = score_assignment_move_cost(current_owner="node-a", target_owner="node-a", shard=(0, 12))
    cost_move = score_assignment_move_cost(current_owner="node-a", target_owner="node-b", shard=(0, 12))

    assert cost_same < cost_move
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_move_cost_prefers_existing_shard_owner
```

Expected: FAIL because move-cost scoring is missing

**Step 3: Write minimal implementation**

```python
# inference/ClusterPlanner.py

def score_assignment_move_cost(current_owner: str | None, target_owner: str, shard: tuple[int, int]) -> float:
    if current_owner is None:
        return 1.0
    if current_owner == target_owner:
        return 0.0
    width = shard[1] - shard[0]
    return 10.0 + width
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_move_cost_prefers_existing_shard_owner
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterPlanner.py test/p2p/test_sticky_replanner.py
git commit -m "feat: add sticky move-cost scoring"
```

---

### Task 10: Add smart replanner with minimal-movement preference

**Files:**
- Modify: `inference/ClusterPlanner.py`
- Modify: `inference/ClusterTypes.py`
- Test: `test/p2p/test_sticky_replanner.py`

**Step 1: Write the failing test**

```python
from inference.ClusterPlanner import replan_distribution
from inference.ClusterTypes import NodeProfile, PlacementPlan, ShardAssignment


def test_replanner_keeps_existing_assignments_when_capacity_still_fits():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )
    nodes = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 28, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 27, loaded_shards=[(12, 24)]),
    ]

    new_plan = replan_distribution(current=current, nodes=nodes, total_layers=24)

    assert [(a.node_id, a.start_layer, a.end_layer) for a in new_plan.assignments] == [
        ("node-a", 0, 12), ("node-b", 12, 24)
    ]
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_replanner_keeps_existing_assignments_when_capacity_still_fits
```

Expected: FAIL because replanner is missing

**Step 3: Write minimal implementation**

```python
# inference/ClusterPlanner.py

def replan_distribution(current, nodes, total_layers):
    node_ids = {n.node_id for n in nodes}
    all_current_nodes_alive = all(a.node_id in node_ids for a in current.assignments)
    if all_current_nodes_alive:
        return current
    return plan_static_distribution(current.model_id, total_layers, nodes)
```

This is intentionally simple for version 1. Later refinements can:
- rebalance only the missing ranges
- shift the smallest suffix/prefix necessary
- keep first/last shard roles sticky when possible

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_replanner_keeps_existing_assignments_when_capacity_still_fits
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterPlanner.py test/p2p/test_sticky_replanner.py
git commit -m "feat: add minimal-movement replanner"
```

---

### Task 11: Add migration plan metadata so nodes know what to load, keep, or drop

**Files:**
- Modify: `inference/ClusterTypes.py`
- Modify: `inference/ClusterCoordinator.py`
- Test: `test/p2p/test_sticky_replanner.py`

**Step 1: Write the failing test**

```python
from inference.ClusterCoordinator import diff_assignments
from inference.ClusterTypes import ShardAssignment


def test_diff_assignments_marks_keep_and_load_actions():
    old = [
        ShardAssignment("node-a", 0, 12),
        ShardAssignment("node-b", 12, 24),
    ]
    new = [
        ShardAssignment("node-a", 0, 12),
        ShardAssignment("node-c", 12, 24),
    ]

    diff = diff_assignments(old, new)

    assert diff["node-a"][0]["action"] == "keep"
    assert diff["node-c"][0]["action"] == "load"
    assert diff["node-b"][0]["action"] == "unload"
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_diff_assignments_marks_keep_and_load_actions
```

Expected: FAIL because diffing is missing

**Step 3: Write minimal implementation**

```python
# inference/ClusterCoordinator.py

def diff_assignments(old_assignments, new_assignments):
    old_map = {(a.node_id, a.start_layer, a.end_layer): a for a in old_assignments}
    new_map = {(a.node_id, a.start_layer, a.end_layer): a for a in new_assignments}
    result = {}

    for key, assignment in new_map.items():
        node_id = assignment.node_id
        action = "keep" if key in old_map else "load"
        result.setdefault(node_id, []).append({"action": action, "assignment": assignment})

    for key, assignment in old_map.items():
        if key not in new_map:
            result.setdefault(assignment.node_id, []).append({"action": "unload", "assignment": assignment})

    return result
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_diff_assignments_marks_keep_and_load_actions
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_sticky_replanner.py
git commit -m "feat: add assignment diff metadata"
```

---

## Phase 3 — Stability (join/leave, barriers, controlled reconfiguration)

### Task 12: Add cluster membership state machine

**Files:**
- Modify: `inference/ClusterCoordinator.py`
- Test: `test/p2p/test_cluster_stability.py`

**Step 1: Write the failing test**

```python
from inference.ClusterCoordinator import ClusterCoordinator


def test_membership_state_tracks_online_nodes():
    coordinator = ClusterCoordinator(transport=None, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24)

    coordinator.node_joined("node-a")
    coordinator.node_joined("node-b")
    coordinator.node_left("node-b")

    assert coordinator.online_nodes == {"node-a"}
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_stability.py::test_membership_state_tracks_online_nodes
```

Expected: FAIL because membership tracking is missing

**Step 3: Write minimal implementation**

```python
class ClusterCoordinator:
    def __init__(...):
        ...
        self.online_nodes = set()

    def node_joined(self, node_id: str):
        self.online_nodes.add(node_id)

    def node_left(self, node_id: str):
        self.online_nodes.discard(node_id)
        self.ready_nodes.discard(node_id)
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_stability.py::test_membership_state_tracks_online_nodes
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_cluster_stability.py
git commit -m "feat: add cluster membership tracking"
```

---

### Task 13: Add heartbeat timeout and re-plan trigger

**Files:**
- Modify: `inference/ClusterCoordinator.py`
- Test: `test/p2p/test_cluster_stability.py`

**Step 1: Write the failing test**

```python
from datetime import datetime, timedelta
from inference.ClusterCoordinator import ClusterCoordinator


def test_heartbeat_timeout_marks_node_stale():
    coordinator = ClusterCoordinator(transport=None, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24)
    coordinator.record_heartbeat("node-a", now=datetime(2026, 3, 24, 10, 0, 0))

    stale = coordinator.find_stale_nodes(now=datetime(2026, 3, 24, 10, 0, 10), timeout_seconds=5)

    assert stale == {"node-a"}
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_stability.py::test_heartbeat_timeout_marks_node_stale
```

Expected: FAIL because heartbeat tracking is missing

**Step 3: Write minimal implementation**

```python
from datetime import datetime

class ClusterCoordinator:
    def __init__(...):
        ...
        self.last_heartbeat = {}

    def record_heartbeat(self, node_id: str, now=None):
        self.last_heartbeat[node_id] = now or datetime.utcnow()

    def find_stale_nodes(self, now=None, timeout_seconds: int = 5):
        now = now or datetime.utcnow()
        return {
            node_id
            for node_id, seen_at in self.last_heartbeat.items()
            if (now - seen_at).total_seconds() > timeout_seconds
        }
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_stability.py::test_heartbeat_timeout_marks_node_stale
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_cluster_stability.py
git commit -m "feat: add heartbeat timeout detection"
```

---

### Task 14: Pause inference while reconfiguration is in progress

**Files:**
- Modify: `inference/ClusterCoordinator.py`
- Modify: `inference/DistributedEngine.py`
- Test: `test/p2p/test_cluster_stability.py`

**Step 1: Write the failing test**

```python
from inference.ClusterCoordinator import ClusterCoordinator


def test_reconfiguration_blocks_new_inference_requests():
    coordinator = ClusterCoordinator(transport=None, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24)
    coordinator.begin_reconfiguration()

    assert coordinator.can_start_inference() is False
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_stability.py::test_reconfiguration_blocks_new_inference_requests
```

Expected: FAIL because the reconfiguration gate does not exist

**Step 3: Write minimal implementation**

```python
class ClusterCoordinator:
    def __init__(...):
        ...
        self.reconfiguring = False

    def begin_reconfiguration(self):
        self.reconfiguring = True

    def finish_reconfiguration(self):
        self.reconfiguring = False

    def can_start_inference(self) -> bool:
        return not self.reconfiguring
```

Update `DistributedInferenceEngine.generate_stream(...)` to fail fast or wait if the coordinator reports reconfiguration in progress.

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_stability.py::test_reconfiguration_blocks_new_inference_requests
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py inference/DistributedEngine.py test/p2p/test_cluster_stability.py
git commit -m "feat: gate inference during reconfiguration"
```

---

### Task 15: Add a full stability scenario test (join → plan → ready → leave → re-plan)

**Files:**
- Modify: `test/p2p/test_cluster_stability.py`
- Modify: `inference/ClusterCoordinator.py`
- Modify: `inference/ClusterPlanner.py`

**Step 1: Write the failing test**

```python
def test_cluster_replans_after_node_leave_with_minimal_disruption():
    # Start with nodes a/b/c
    # Build initial plan
    # Mark c as left
    # Replan
    # Verify surviving shards remain sticky where possible
    assert False
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_stability.py::test_cluster_replans_after_node_leave_with_minimal_disruption
```

Expected: FAIL because the full scenario is not implemented yet

**Step 3: Write minimal implementation**

Implement only enough glue to satisfy the scenario:
- coordinator removes departed node
- planner reuses surviving assignments when possible
- coordinator emits diff metadata
- ready barrier resets for the new plan

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_stability.py::test_cluster_replans_after_node_leave_with_minimal_disruption
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py inference/ClusterPlanner.py test/p2p/test_cluster_stability.py
git commit -m "feat: add cluster leave and replan flow"
```

---

## Final integration and documentation

### Task 16: Update exports, docs, and research diary

**Files:**
- Modify: `inference/__init__.py`
- Modify: `inference/README.md`
- Modify: `docs/README.md`
- Modify: `docs/diary/2026-03-24.md`

**Step 1: Write the failing doc checklist**

```markdown
- [ ] README explains control plane vs data plane
- [ ] README explains static planner vs smart replanner
- [ ] README documents join/leave stability limits
- [ ] Diary records the new cluster-planning findings
```

**Step 2: Run doc verification**

Run:
```bash
rg -n "control plane|smart replanner|heartbeat|reconfiguration" inference/README.md docs/README.md docs/diary/2026-03-24.md
```

Expected: missing matches before edits

**Step 3: Write minimal documentation updates**

Document:
- static cluster workflow
- coordinator election by strongest node
- minimal-movement re-planning
- stability model: join/leave trigger reconfiguration barrier, then reload

**Step 4: Run doc verification**

Run:
```bash
rg -n "control plane|smart replanner|heartbeat|reconfiguration" inference/README.md docs/README.md docs/diary/2026-03-24.md
```

Expected: matches found in all three docs

**Step 5: Commit**

```bash
git add inference/__init__.py inference/README.md docs/README.md docs/diary/2026-03-24.md
git commit -m "docs: document distributed cluster planning and stability"
```

---

## Verification matrix

Run these after each phase, not just at the end.

### Phase 1 verification
```bash
python3.11 -m pytest -q test/p2p/test_device_profile.py test/p2p/test_static_cluster_planner.py test/p2p/test_cluster_coordinator.py
```

### Phase 2 verification
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py
```

### Phase 3 verification
```bash
python3.11 -m pytest -q test/p2p/test_cluster_stability.py
```

### End-to-end inference verification
```bash
python3.11 -m pytest -q test/inference test/backend test/p2p
```

### Manual verification checklist
- Start three nodes with distinct `node_id`s
- Confirm strongest node becomes coordinator
- Confirm coordinator emits one static plan
- Confirm each node only loads its assigned shard
- Run one prompt through the real remote path
- Simulate one node leaving
- Confirm reconfiguration blocks new inference until ready
- Confirm the new plan moves as few shards as possible

---

## Implementation notes for the engineer

### Why this plan starts with static distribution
Static distribution proves the real control loop without hiding complexity behind “smart” behavior. If this step is skipped, later failures will be ambiguous: planner bug, transport bug, readiness bug, or inference bug.

### Why smart planning is separate
Minimal-movement re-planning is a policy layer, not the foundation. It should be added only after the static plan / broadcast / load / ready flow is already trustworthy.

### Why stability is barrier-based
Do not try to support seamless hot migration first. The safer first version is:
1. detect join/leave,
2. pause new inference,
3. compute new plan,
4. load/unload shards,
5. wait for ready,
6. resume inference.

This is simpler to verify and much less likely to corrupt KV-cache or route state.

### How this connects to current code
- `inference/ShardLoader.py` stays the shard materialization mechanism.
- `inference/DistributedEngine.py` evolves from simulation-only into local+remote execution.
- `p2p/P2PClient.py` must stop being a model runner and become a generic transport utility.
- `ModelSplitter` remains legacy and should not be expanded.

