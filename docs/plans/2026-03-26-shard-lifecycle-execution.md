# Shard Lifecycle Execution Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current reconfiguration diff from metadata into a real shard lifecycle flow where `keep`, `load`, `move_in`, and `unload` drive node-local shard state, coordinator barriers, and safe plan cutover.

**Architecture:** Keep `inference/ClusterPlanner.py` responsible only for placement and diff generation. Add execution semantics in `inference/NodeRuntime.py` and orchestration semantics in `inference/ClusterCoordinator.py`. The first real version should use a two-phase reconfiguration model: `prepare` loads or validates shards on destination nodes, `commit` switches ownership, and only then do old owners release shards. `move_in` in v1 is implemented as destination-side local reload via `ShardLoader`, not peer-to-peer weight transfer.

**Tech Stack:** Python 3.11, dataclasses, pytest, existing control-plane modules in `inference/` and p2p tests in `test/p2p/`.

---

## Scope and defaults

This plan does not introduce true weight streaming between peers. A `move_in` action means “destination reloads the shard locally, then becomes active after commit.” A node must never unload a shard before the replacement owner is prepared and the coordinator commits the new configuration.

The execution state model is fixed for this plan:
- `absent`
- `loading`
- `ready`
- `serving`
- `draining`
- `releasing`

The transport protocol is also fixed in v1:
- `cluster_reconfigure_prepare`
- `cluster_reconfigure_commit`

The coordinator remains single-leader and handles one reconfiguration at a time.

---

### Task 1: Add node-local shard lifecycle state and registry

**Files:**
- Create: `inference/ShardRegistry.py`
- Modify: `inference/NodeRuntime.py`
- Test: `test/p2p/test_runtime_lifecycle.py`

**Step 1: Write the failing test**

```python
from inference.ShardRegistry import ShardRecord, ShardRegistry


def test_shard_registry_tracks_lifecycle_states_by_range():
    registry = ShardRegistry()
    registry.put(
        ShardRecord(
            shard_key=(0, 12),
            role="first",
            state="loading",
            shard_obj="demo-shard",
            active_owner=False,
        )
    )

    record = registry.get((0, 12))

    assert record is not None
    assert record.state == "loading"
    assert record.active_owner is False
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_runtime_lifecycle.py::test_shard_registry_tracks_lifecycle_states_by_range
```

Expected: FAIL because `ShardRegistry` does not exist.

**Step 3: Write minimal implementation**

Create `inference/ShardRegistry.py`:

```python
from dataclasses import dataclass


@dataclass
class ShardRecord:
    shard_key: tuple[int, int]
    role: str
    state: str
    shard_obj: object | None = None
    active_owner: bool = False
    pending_unload: bool = False


class ShardRegistry:
    def __init__(self):
        self._records: dict[tuple[int, int], ShardRecord] = {}

    def get(self, shard_key: tuple[int, int]) -> ShardRecord | None:
        return self._records.get(shard_key)

    def put(self, record: ShardRecord) -> None:
        self._records[record.shard_key] = record

    def remove(self, shard_key: tuple[int, int]) -> None:
        self._records.pop(shard_key, None)

    def all_records(self) -> list[ShardRecord]:
        return list(self._records.values())
```

Add a `registry` field to `NodeRuntime` initialized with `ShardRegistry()`.

**Step 4: Run test to verify it passes**

Run the same pytest command again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/ShardRegistry.py inference/NodeRuntime.py test/p2p/test_runtime_lifecycle.py
git commit -m "feat: add shard lifecycle registry"
```

---

### Task 2: Implement prepare-phase execution for `keep` and `load`

**Files:**
- Modify: `inference/NodeRuntime.py`
- Test: `test/p2p/test_runtime_lifecycle.py`

**Step 1: Write the failing tests**

```python
def test_prepare_load_transitions_absent_to_ready_and_stores_shard():
    loader = FakeLoader()
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=loader)

    actions = [
        NodeReconfigurationAction(
            node_id="node-a",
            action="load",
            start_layer=0,
            end_layer=12,
            role="first",
        )
    ]

    result = runtime.prepare_reconfiguration(model_id="Qwen/Qwen2.5-0.5B-Instruct", actions=actions)

    record = runtime.registry.get((0, 12))
    assert result[0]["status"] == "ready"
    assert loader.loaded.start_layer == 0
    assert record is not None
    assert record.state == "ready"


def test_prepare_keep_validates_existing_serving_shard_without_reloading():
    loader = FakeLoader()
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=loader)
    runtime.registry.put(ShardRecord((0, 12), role="first", state="serving", shard_obj="warm", active_owner=True))

    actions = [NodeReconfigurationAction("node-a", "keep", 0, 12, role="first")]
    result = runtime.prepare_reconfiguration(model_id="Qwen/Qwen2.5-0.5B-Instruct", actions=actions)

    assert result[0]["status"] == "ready"
    assert loader.loaded is None
    assert runtime.registry.get((0, 12)).state == "serving"
```

**Step 2: Run tests to verify they fail**

Run the two tests. Expected: FAIL because `prepare_reconfiguration` does not exist.

**Step 3: Write minimal implementation**

In `inference/NodeRuntime.py`, add:

```python
def prepare_reconfiguration(self, model_id: str, actions: list[NodeReconfigurationAction]):
    results = []
    for action in actions:
        shard_key = (action.start_layer, action.end_layer)
        if action.action == "keep":
            record = self.registry.get(shard_key)
            if record is None:
                raise RuntimeError(f"Missing shard for keep: {shard_key}")
            results.append({"shard_key": shard_key, "status": "ready", "action": "keep"})
            continue
        if action.action in {"load", "move_in"}:
            self.registry.put(ShardRecord(shard_key, role=action.role, state="loading", active_owner=False))
            assignment = ShardAssignment(
                node_id=self.node_id,
                start_layer=action.start_layer,
                end_layer=action.end_layer,
                role=action.role,
            )
            self.apply_assignment(model_id=model_id, assignment=assignment)
            self.registry.put(ShardRecord(shard_key, role=action.role, state="ready", shard_obj=self.local_shard, active_owner=False))
            results.append({"shard_key": shard_key, "status": "ready", "action": action.action})
            continue
        if action.action == "unload":
            record = self.registry.get(shard_key)
            if record is not None:
                record.pending_unload = True
            results.append({"shard_key": shard_key, "status": "deferred", "action": "unload"})
    return results
```

If `ShardRecord` remains frozen, replace in-place mutation with a new record written back to the registry.

**Step 4: Run tests to verify they pass**

Run the same two tests again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/NodeRuntime.py test/p2p/test_runtime_lifecycle.py
git commit -m "feat: add prepare-phase load and keep execution"
```

---

### Task 3: Implement prepare-phase `move_in` and deferred `unload`

**Files:**
- Modify: `inference/NodeRuntime.py`
- Test: `test/p2p/test_runtime_lifecycle.py`

**Step 1: Write the failing tests**

```python
def test_prepare_move_in_uses_local_reload_and_stays_non_active_until_commit():
    loader = FakeLoader()
    runtime = NodeRuntime(node_id="node-c", family=ModelFamily.QWEN, total_layers=24, loader=loader)

    actions = [NodeReconfigurationAction("node-c", "move_in", 0, 12, role="first", from_node_id="node-a")]
    runtime.prepare_reconfiguration(model_id="Qwen/Qwen2.5-0.5B-Instruct", actions=actions)

    record = runtime.registry.get((0, 12))
    assert loader.loaded.start_layer == 0
    assert record.state == "ready"
    assert record.active_owner is False


def test_prepare_unload_marks_pending_but_does_not_remove_serving_shard():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(ShardRecord((0, 12), role="first", state="serving", shard_obj="warm", active_owner=True))

    actions = [NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")]
    runtime.prepare_reconfiguration(model_id="Qwen/Qwen2.5-0.5B-Instruct", actions=actions)

    record = runtime.registry.get((0, 12))
    assert record is not None
    assert record.pending_unload is True
    assert record.state == "serving"
```

**Step 2: Run tests to verify they fail**

Run the two tests. Expected: FAIL until `move_in` and `unload` semantics are real.

**Step 3: Write minimal implementation**

Refine `prepare_reconfiguration(...)` so:
- `move_in` uses the same local reload path as `load`, but leaves `active_owner=False`
- `unload` does not delete the shard during prepare; it marks the registry record as `pending_unload=True`

**Step 4: Run tests to verify they pass**

Run the same tests again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/NodeRuntime.py test/p2p/test_runtime_lifecycle.py
git commit -m "feat: add prepare-phase move-in and deferred unload"
```

---

### Task 4: Add coordinator-side prepare barrier and commit payload

**Files:**
- Modify: `inference/ClusterCoordinator.py`
- Modify: `test/p2p/test_cluster_coordinator.py`

**Step 1: Write the failing tests**

```python
def test_prepare_reconfiguration_waits_for_ready_before_commit():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(transport=transport, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24)
    reconfiguration = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        actions_by_node={
            "node-a": [NodeReconfigurationAction("node-a", "keep", 0, 12, role="first")],
            "node-c": [NodeReconfigurationAction("node-c", "move_in", 12, 24, role="last", from_node_id="node-b")],
        },
    )

    coordinator.begin_reconfiguration(reconfiguration)
    assert transport.sent[0][0] == "cluster_reconfigure_prepare"

    coordinator.mark_reconfig_ready("node-a")
    assert coordinator.reconfiguration_committed is False

    coordinator.mark_reconfig_ready("node-c")
    assert transport.sent[-1][0] == "cluster_reconfigure_commit"
    assert coordinator.reconfiguration_committed is True
```

**Step 2: Run test to verify it fails**

Run the single test. Expected: FAIL because prepare/commit orchestration does not exist.

**Step 3: Write minimal implementation**

In `inference/ClusterCoordinator.py`, add fields:

```python
self.pending_reconfiguration: ReconfigurationPlan | None = None
self.reconfig_ready_nodes: set[str] = set()
self.reconfiguration_committed: bool = False
```

Add methods:

```python
def begin_reconfiguration(self, reconfiguration: ReconfigurationPlan):
    self.pending_reconfiguration = reconfiguration
    self.reconfig_ready_nodes = set()
    self.reconfiguration_committed = False
    self.transport.broadcast("cluster_reconfigure_prepare", {...})


def mark_reconfig_ready(self, node_id: str):
    self.reconfig_ready_nodes.add(node_id)
    if self.pending_reconfiguration is not None:
        expected = set(self.pending_reconfiguration.actions_by_node.keys())
        if expected.issubset(self.reconfig_ready_nodes):
            self.transport.broadcast("cluster_reconfigure_commit", {...})
            self.reconfiguration_committed = True
```

The commit payload only needs `model_id`, `coordinator_id`, and maybe `actions_by_node` again in v1.

**Step 4: Run test to verify it passes**

Run the same test. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_cluster_coordinator.py
git commit -m "feat: add reconfiguration prepare and commit barrier"
```

---

### Task 5: Make runtime react to commit and release pending shards

**Files:**
- Modify: `inference/NodeRuntime.py`
- Test: `test/p2p/test_runtime_lifecycle.py`

**Step 1: Write the failing tests**

```python
def test_commit_promotes_ready_move_in_to_serving_owner():
    runtime = NodeRuntime(node_id="node-c", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(ShardRecord((0, 12), role="first", state="ready", shard_obj="warm", active_owner=False))

    runtime.commit_reconfiguration([NodeReconfigurationAction("node-c", "move_in", 0, 12, role="first", from_node_id="node-a")])

    record = runtime.registry.get((0, 12))
    assert record.state == "serving"
    assert record.active_owner is True


def test_commit_unload_releases_pending_shard():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(ShardRecord((0, 12), role="first", state="serving", shard_obj="warm", active_owner=True, pending_unload=True))

    runtime.commit_reconfiguration([NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")])

    assert runtime.registry.get((0, 12)) is None
```

**Step 2: Run tests to verify they fail**

Run the two tests. Expected: FAIL because commit execution does not exist.

**Step 3: Write minimal implementation**

In `inference/NodeRuntime.py`, add:

```python
def commit_reconfiguration(self, actions: list[NodeReconfigurationAction]):
    for action in actions:
        shard_key = (action.start_layer, action.end_layer)
        record = self.registry.get(shard_key)
        if action.action in {"keep", "load", "move_in"}:
            if record is None:
                raise RuntimeError(f"Missing shard during commit: {shard_key}")
            self.registry.put(ShardRecord(
                shard_key=record.shard_key,
                role=record.role,
                state="serving",
                shard_obj=record.shard_obj,
                active_owner=True,
                pending_unload=False,
            ))
        elif action.action == "unload":
            if record is not None and record.pending_unload:
                self.registry.remove(shard_key)
```

Register a `cluster_reconfigure_commit` handler in `attach_transport(...)` and add `handle_reconfiguration_commit(payload)` that filters node-local actions and calls `commit_reconfiguration(...)`.

**Step 4: Run tests to verify they pass**

Run the same tests. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/NodeRuntime.py test/p2p/test_runtime_lifecycle.py
git commit -m "feat: commit reconfiguration lifecycle transitions"
```

---

### Task 6: Add end-to-end lifecycle smoke coverage and docs

**Files:**
- Modify: `test/p2p/test_cluster_coordinator.py`
- Modify: `inference/__init__.py`
- Modify: `inference/README.md`
- Modify: `docs/README.md`
- Modify: `docs/diary/2026-03-26.md`

**Step 1: Write the failing smoke test**

```python
def test_reconfiguration_prepare_then_commit_keeps_old_until_new_ready():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(transport=transport, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24)
    loader_a = FakeLoader()
    loader_c = FakeLoader()
    runtime_a = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=loader_a)
    runtime_c = NodeRuntime(node_id="node-c", family=ModelFamily.QWEN, total_layers=24, loader=loader_c)
    runtime_a.attach_transport(transport, on_ready=coordinator.mark_reconfig_ready)
    runtime_c.attach_transport(transport, on_ready=coordinator.mark_reconfig_ready)

    # seed old owner
    runtime_a.registry.put(ShardRecord((0, 12), role="first", state="serving", shard_obj="warm", active_owner=True))

    reconfiguration = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-c",
        actions_by_node={
            "node-a": [NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")],
            "node-c": [NodeReconfigurationAction("node-c", "move_in", 0, 12, role="first", from_node_id="node-a")],
        },
    )

    coordinator.begin_reconfiguration(reconfiguration)

    assert runtime_a.registry.get((0, 12)) is not None
    assert runtime_c.registry.get((0, 12)).state == "serving"
```

Adjust the final assertion if commit happens only after both ready callbacks fire during the transport broadcast.

**Step 2: Run test to verify it fails**

Run the single smoke test. Expected: FAIL until prepare + commit are wired end to end.

**Step 3: Write minimal implementation**

Export `ShardRegistry` / `ShardRecord` if the test imports them through `inference`. Update README/docs/diary to explain:
- lifecycle states now exist
- reconfiguration has `prepare` and `commit`
- `move_in` is destination-side reload in v1
- `unload` happens after commit, not before

**Step 4: Run final verification**

Run:
```bash
python3.11 -m py_compile inference/ShardRegistry.py inference/NodeRuntime.py inference/ClusterCoordinator.py
python3.11 -m pytest -q test/p2p/test_runtime_lifecycle.py test/p2p/test_cluster_coordinator.py test/p2p/test_cluster_replan_flow.py
```

Expected: all PASS.

**Step 5: Commit**

```bash
git add inference/ShardRegistry.py inference/NodeRuntime.py inference/ClusterCoordinator.py inference/__init__.py inference/README.md docs/README.md docs/diary/2026-03-26.md test/p2p/test_runtime_lifecycle.py test/p2p/test_cluster_coordinator.py
 git commit -m "feat: execute shard lifecycle reconfiguration"
```

---

## Acceptance criteria

1. A node can maintain shard lifecycle state in a local registry.
2. `load` and `move_in` create locally prepared shards before cutover.
3. `unload` is deferred until commit and never deletes the old shard during prepare.
4. Coordinator performs a `prepare` broadcast, waits for ready, then emits `commit`.
5. Runtime can react to both prepare and commit payloads.
6. End-to-end tests prove old ownership is not dropped before replacement is ready.

## Assumptions and defaults

- `move_in` in this phase is implemented as destination-local reload, not network weight copy.
- Only one reconfiguration can be pending at a time.
- This plan does not add mid-token request draining yet; it only guarantees prepare-before-unload at the control-plane level.
- If a shard marked for `keep` is missing locally, runtime should fail loudly instead of silently reloading it.
- Real load/move/unload telemetry can be added later; v1 only needs deterministic state transitions and tests.
