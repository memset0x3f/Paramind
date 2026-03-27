# Runtime Safety Core Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade shard reconfiguration from control-plane correctness to runtime-safe execution by adding inflight tracking, draining, delayed release, and coordinator-visible phase/timeline state.

**Architecture:** Keep placement and diff generation in `inference/ClusterPlanner.py`, and keep lifecycle execution in `inference/NodeRuntime.py`, `inference/ShardRegistry.py`, and `inference/ClusterCoordinator.py`. The key idea is that `commit` should make the new owner active for new work while the old owner transitions to `draining`; only after the old shard's inflight count reaches zero may a pending unload be finalized. Observability is part of the core: runtime/coordinator state must be queryable through stable debug snapshots and timestamped reconfiguration metadata.

**Tech Stack:** Python 3.11, dataclasses, pytest, existing inference control-plane modules, existing lifecycle tests under `test/p2p/`.

---

## Scope and defaults

This plan is intentionally limited to `inference/` runtime safety and observability. It does not add retry logic, transport recovery, or peer-to-peer weight transfer; those remain `p2p/` concerns. `move_in` continues to mean destination-local reload in v1. The runtime-safety milestone is: old owners do not drop active requests during reconfiguration, and the system can explain why a shard is still present or why it has been released.

The core states for a shard record in this phase are:
- `ready`
- `serving`
- `draining`
- `releasing`

The required counters/flags are:
- `inflight_requests`
- `pending_unload`
- `active_owner`

The coordinator must track, at minimum:
- `reconfiguration_id`
- `phase` (`idle`, `prepare`, `commit`, `complete`)
- `prepare_started_at`
- `commit_started_at`
- `ready_nodes`
- `reconfig_ready_nodes`

---

### Task 1: Extend shard records with inflight and phase-safe metadata

**Files:**
- Modify: `inference/ShardRegistry.py`
- Modify: `test/p2p/test_runtime_lifecycle.py`

**Step 1: Write the failing tests**

```python
def test_shard_record_defaults_include_inflight_and_timestamps():
    record = ShardRecord(shard_key=(0, 12), role="first", state="serving")

    assert record.inflight_requests == 0
    assert record.pending_unload is False
    assert record.last_prepare_ts is None
    assert record.last_commit_ts is None


def test_registry_replace_updates_existing_record_for_same_shard_key():
    registry = ShardRegistry()
    registry.put(ShardRecord((0, 12), role="first", state="ready", inflight_requests=0))
    registry.put(ShardRecord((0, 12), role="first", state="serving", inflight_requests=2))

    record = registry.get((0, 12))
    assert record is not None
    assert record.state == "serving"
    assert record.inflight_requests == 2
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_runtime_lifecycle.py::test_shard_record_defaults_include_inflight_and_timestamps test/p2p/test_runtime_lifecycle.py::test_registry_replace_updates_existing_record_for_same_shard_key
```

Expected: FAIL because the extra fields do not exist yet.

**Step 3: Write minimal implementation**

In `inference/ShardRegistry.py`, extend `ShardRecord` with:

```python
inflight_requests: int = 0
last_prepare_ts: float | None = None
last_commit_ts: float | None = None
```

Keep the registry map keyed by shard range and preserve overwrite semantics.

**Step 4: Run test to verify it passes**

Run the same pytest command again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/ShardRegistry.py test/p2p/test_runtime_lifecycle.py
git commit -m "feat: add inflight-aware shard records"
```

---

### Task 2: Add request enter/exit accounting and delayed release in NodeRuntime

**Files:**
- Modify: `inference/NodeRuntime.py`
- Modify: `test/p2p/test_runtime_lifecycle.py`

**Step 1: Write the failing tests**

```python
def test_begin_request_increments_inflight_on_serving_shard():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(ShardRecord((0, 12), role="first", state="serving", active_owner=True))

    runtime.begin_request((0, 12))

    assert runtime.registry.get((0, 12)).inflight_requests == 1


def test_finish_request_releases_draining_shard_when_inflight_reaches_zero():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="draining",
            shard_obj="warm",
            active_owner=False,
            pending_unload=True,
            inflight_requests=1,
        )
    )

    runtime.finish_request((0, 12))

    assert runtime.registry.get((0, 12)) is None
```

**Step 2: Run tests to verify they fail**

Run those two tests. Expected: FAIL because `begin_request`/`finish_request` do not exist.

**Step 3: Write minimal implementation**

In `inference/NodeRuntime.py`, add:
- `begin_request(shard_key)`
- `finish_request(shard_key)`
- private helper like `_release_if_drained(shard_key)`

Behavior:
- `begin_request` requires the shard record to exist and be `serving`
- increments `inflight_requests`
- `finish_request` decrements but never below zero
- if record is `draining` and `pending_unload` and inflight reaches zero, remove it

**Step 4: Run tests to verify they pass**

Run the same tests again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/NodeRuntime.py test/p2p/test_runtime_lifecycle.py
git commit -m "feat: track inflight shard requests"
```

---

### Task 3: Make commit move old owners into draining instead of immediate removal

**Files:**
- Modify: `inference/NodeRuntime.py`
- Modify: `test/p2p/test_runtime_lifecycle.py`

**Step 1: Write the failing tests**

```python
def test_commit_unload_keeps_shard_when_inflight_requests_exist_and_marks_draining():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="serving",
            shard_obj="warm",
            active_owner=True,
            pending_unload=True,
            inflight_requests=2,
        )
    )

    runtime.commit_reconfiguration([NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")])

    record = runtime.registry.get((0, 12))
    assert record is not None
    assert record.state == "draining"
    assert record.active_owner is False


def test_commit_unload_drops_immediately_when_no_inflight_requests_exist():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="serving",
            shard_obj="warm",
            active_owner=True,
            pending_unload=True,
            inflight_requests=0,
        )
    )

    runtime.commit_reconfiguration([NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")])

    assert runtime.registry.get((0, 12)) is None
```

**Step 2: Run tests to verify they fail**

Run the two tests. Expected: FAIL because current commit path removes too early.

**Step 3: Write minimal implementation**

Refine `commit_reconfiguration(...)` in `inference/NodeRuntime.py` so that:
- `keep` / `load` / `move_in` still become `serving` and `active_owner=True`
- `unload` checks `inflight_requests`
- if `inflight_requests > 0`, rewrite the record to `state="draining"`, `active_owner=False`, `pending_unload=True`
- if `inflight_requests == 0`, remove immediately

Set `last_commit_ts` for all commit-touched records.

**Step 4: Run tests to verify they pass**

Run the same tests again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/NodeRuntime.py test/p2p/test_runtime_lifecycle.py
git commit -m "feat: drain old shards before release"
```

---

### Task 4: Add runtime debug snapshots for shard lifecycle observability

**Files:**
- Modify: `inference/NodeRuntime.py`
- Modify: `test/p2p/test_runtime_lifecycle.py`

**Step 1: Write the failing tests**

```python
def test_node_runtime_debug_snapshot_reports_shard_state_and_inflight():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="draining",
            active_owner=False,
            pending_unload=True,
            inflight_requests=2,
        )
    )

    snapshot = runtime.debug_snapshot()

    assert snapshot["node_id"] == "node-a"
    assert snapshot["shards"][0]["state"] == "draining"
    assert snapshot["shards"][0]["inflight_requests"] == 2
    assert snapshot["shards"][0]["pending_unload"] is True
```

**Step 2: Run test to verify it fails**

Run the single test. Expected: FAIL because `debug_snapshot()` does not exist.

**Step 3: Write minimal implementation**

Add `debug_snapshot()` to `inference/NodeRuntime.py`. It should return a plain dict containing:
- `node_id`
- `assignment` summary or `None`
- `shards`: sorted list of records with `shard_key`, `role`, `state`, `active_owner`, `pending_unload`, `inflight_requests`, `last_prepare_ts`, `last_commit_ts`

**Step 4: Run test to verify it passes**

Run the same test again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/NodeRuntime.py test/p2p/test_runtime_lifecycle.py
git commit -m "feat: add runtime lifecycle snapshots"
```

---

### Task 5: Add coordinator-side reconfiguration timeline and debug snapshot

**Files:**
- Modify: `inference/ClusterCoordinator.py`
- Modify: `test/p2p/test_cluster_coordinator.py`

**Step 1: Write the failing tests**

```python
def test_begin_reconfiguration_sets_phase_and_timestamps():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(transport=transport, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24)
    reconfiguration = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        actions_by_node={"node-a": [NodeReconfigurationAction("node-a", "keep", 0, 12, role="first")]},
    )

    coordinator.begin_reconfiguration(reconfiguration)

    snapshot = coordinator.debug_snapshot()
    assert snapshot["phase"] == "prepare"
    assert snapshot["reconfiguration_id"] is not None
    assert snapshot["prepare_started_at"] is not None


def test_mark_reconfig_ready_moves_phase_to_commit_when_all_nodes_ready():
    transport = FakeTransport()
    coordinator = ClusterCoordinator(transport=transport, model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24)
    reconfiguration = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        actions_by_node={"node-a": [NodeReconfigurationAction("node-a", "keep", 0, 12, role="first")]},
    )

    coordinator.begin_reconfiguration(reconfiguration)
    coordinator.mark_reconfig_ready("node-a")

    snapshot = coordinator.debug_snapshot()
    assert snapshot["phase"] in {"commit", "complete"}
    assert snapshot["commit_started_at"] is not None
```

**Step 2: Run tests to verify they fail**

Run those tests. Expected: FAIL because the phase/timeline fields do not exist.

**Step 3: Write minimal implementation**

In `inference/ClusterCoordinator.py`, add:
- `reconfiguration_id`
- `phase`
- `prepare_started_at`
- `commit_started_at`
- `completed_at` optional
- `debug_snapshot()`

Behavior:
- `begin_reconfiguration(...)` sets a new id and enters `prepare`
- `mark_reconfig_ready(...)` sets `commit_started_at` before broadcasting commit
- if all nodes are ready and commit is broadcast, phase becomes `commit` or `complete`

Use a simple monotonically increasing integer or timestamp-based id in v1.

**Step 4: Run tests to verify they pass**

Run the same tests again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_cluster_coordinator.py
git commit -m "feat: add coordinator reconfiguration timeline"
```

---

### Task 6: Prove commit shifts new requests to the new owner while old owner drains

**Files:**
- Modify: `test/p2p/test_cluster_coordinator.py`
- Modify: `test/p2p/test_runtime_lifecycle.py`
- Modify: `inference/NodeRuntime.py`

**Step 1: Write the failing tests**

```python
def test_old_owner_can_finish_existing_request_after_commit_while_new_owner_serves_new_work():
    old_runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    new_runtime = NodeRuntime(node_id="node-c", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())

    old_runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="serving",
            active_owner=True,
            pending_unload=True,
            inflight_requests=1,
        )
    )
    new_runtime.registry.put(
        ShardRecord(
            (0, 12),
            role="first",
            state="ready",
            active_owner=False,
            inflight_requests=0,
        )
    )

    old_runtime.commit_reconfiguration([NodeReconfigurationAction("node-a", "unload", 0, 12, role="first")])
    new_runtime.commit_reconfiguration([NodeReconfigurationAction("node-c", "move_in", 0, 12, role="first", from_node_id="node-a")])

    assert old_runtime.registry.get((0, 12)).state == "draining"
    assert new_runtime.registry.get((0, 12)).state == "serving"

    new_runtime.begin_request((0, 12))
    old_runtime.finish_request((0, 12))

    assert new_runtime.registry.get((0, 12)).inflight_requests == 1
    assert old_runtime.registry.get((0, 12)) is None
```

**Step 2: Run test to verify it fails**

Run the single test. Expected: FAIL until drain/release interplay is correct.

**Step 3: Write minimal implementation**

If necessary, refine `begin_request` so only `serving` shards accept new work, and confirm `draining` shards can only be finished, not newly entered. Adjust `finish_request` and `commit_reconfiguration` as needed so the old shard disappears only after inflight reaches zero.

**Step 4: Run test to verify it passes**

Run the same test again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/NodeRuntime.py test/p2p/test_runtime_lifecycle.py test/p2p/test_cluster_coordinator.py
git commit -m "feat: make reconfiguration drain safe for in-flight work"
```

---

### Task 7: Update inference docs and daily diary for runtime safety core

**Files:**
- Modify: `inference/README.md`
- Modify: `docs/diary/2026-03-26.md`

**Step 1: Write the failing doc assertion**

```python
from pathlib import Path


def test_inference_readme_mentions_draining_and_inflight_runtime_safety():
    text = Path("inference/README.md").read_text()
    assert "draining" in text
    assert "inflight" in text
    assert "debug_snapshot" in text
```

**Step 2: Run test to verify it fails**

Run a small pytest doc assertion or equivalent Python one-liner. Expected: FAIL until docs are updated.

**Step 3: Write minimal documentation updates**

Update the README and diary so they explain:
- what runtime-safe reconfiguration means now
- that old owners drain rather than disappear immediately
- what `debug_snapshot()` exposes
- what remains missing (`mid-token routing`, transport retry, peer copy)

Use relative paths in README references.

**Step 4: Run test to verify it passes**

Run the same check again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/README.md docs/diary/2026-03-26.md
git commit -m "docs: document runtime safety core"
```

---

### Task 8: Final verification

**Files:**
- Verify: `inference/ShardRegistry.py`
- Verify: `inference/NodeRuntime.py`
- Verify: `inference/ClusterCoordinator.py`
- Verify: `test/p2p/test_runtime_lifecycle.py`
- Verify: `test/p2p/test_cluster_coordinator.py`

**Step 1: Run compile checks**

Run:
```bash
python3.11 -m py_compile inference/ShardRegistry.py inference/NodeRuntime.py inference/ClusterCoordinator.py
```

Expected: PASS.

**Step 2: Run targeted tests**

Run:
```bash
python3.11 -m pytest -q --tb=short test/p2p/test_runtime_lifecycle.py test/p2p/test_cluster_coordinator.py test/p2p/test_cluster_replan_flow.py
```

Expected: all PASS.

**Step 3: Run broader control-plane regression**

Run:
```bash
python3.11 -m pytest -q --tb=short test/p2p/test_runtime_lifecycle.py test/p2p/test_cluster_coordinator.py test/p2p/test_cluster_replan_flow.py test/p2p/test_sticky_replanner.py test/p2p/test_static_cluster_planner.py
```

Expected: all PASS.

**Step 4: Commit**

```bash
git add inference/ShardRegistry.py inference/NodeRuntime.py inference/ClusterCoordinator.py inference/README.md docs/diary/2026-03-26.md test/p2p/test_runtime_lifecycle.py test/p2p/test_cluster_coordinator.py
 git commit -m "feat: add runtime-safe shard draining and observability"
```

---

## Acceptance criteria

1. Old shard owners are not immediately removed on commit if they still have inflight requests.
2. Old shard owners transition to `draining`, reject new work, and release only after inflight reaches zero.
3. New owners become `serving` on commit and can accept new work immediately.
4. Runtime debug snapshots expose shard lifecycle state, pending unload, and inflight counts.
5. Coordinator debug snapshots expose reconfiguration id, phase, timestamps, and ready-node state.
6. The runtime safety behavior is covered by targeted tests and documented in `inference/README.md`.

## Assumptions and defaults

- This plan does not add retry logic, rollback logic, transport recovery, or peer-to-peer weight transfer.
- `move_in` remains destination-local reload in v1.
- Request accounting is shard-range scoped, not token-stream scoped.
- Mid-token routing and fully graceful request migration remain future work after this runtime-safety core is stable.
