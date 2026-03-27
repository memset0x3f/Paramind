# Phase 2 Replanner Integration and Smarter Cost Model Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the current minimal sticky replanner into a real control-plane capability by wiring it into the coordinator flow, testing replan-trigger scenarios, and then upgrading the cost model beyond the current simple heuristic.

**Architecture:** Keep the current split between planner and coordinator. `inference/ClusterPlanner.py` remains the pure decision layer (static planning, move-cost scoring, replan logic), while `inference/ClusterCoordinator.py` becomes the orchestration layer that stores the current plan, reacts to topology/profile changes, and requests replans. Phase 2 should happen in two passes: first connect the existing minimal replanner to the control flow and validate join/leave/capacity scenarios; then replace the coarse cost heuristic with a richer cost model that still stays deterministic and testable.

**Tech Stack:** Python 3.11, dataclasses, pytest, current inference control-plane modules (`inference/ClusterPlanner.py`, `inference/ClusterCoordinator.py`, `inference/ClusterTypes.py`, `inference/DeviceProfile.py`), and current P2P/control-plane tests under `test/p2p/`.

---

## Scope and non-goals

### In scope
- Wire `replan_distribution(...)` into `ClusterCoordinator`
- Add control-plane tests for topology/profile changes
- Add a stable API for “replan from current state”
- Expand the planner from simple keep-or-fallback into smarter cost-based scoring
- Document the new Phase 2 behavior in README + daily diary

### Out of scope for this plan
- Real network ACK implementation
- Multi-process transport execution
- Actual shard migration execution (`move`, `load`, `unload` RPC calls)
- Frontend topology visualization
- Full node join/leave fault tolerance during active token generation

### Design constraints
- Reuse existing planner/coordinator files; do not create a second planner abstraction.
- Keep planner functions deterministic and unit-testable.
- Do not overfit the cost model to real hardware yet; use a documented heuristic with explicit inputs.
- Use TDD for every behavior change.
- Keep docs in sync after major code changes: relevant README(s) + same-day diary.

---

## Proposed file map

### Planner / coordinator
- Modify: `inference/ClusterPlanner.py`
- Modify: `inference/ClusterCoordinator.py`
- Modify: `inference/__init__.py`

### Tests
- Modify: `test/p2p/test_cluster_coordinator.py`
- Modify: `test/p2p/test_sticky_replanner.py`
- Create: `test/p2p/test_cluster_replan_flow.py`

### Docs
- Modify: `inference/README.md`
- Modify: `docs/README.md`
- Modify: `docs/diary/2026-03-25.md`

---

## Phase 2A — Connect the current simple replanner to the coordinator

### Task 1: Add coordinator state for current plan and explicit replanning

**Files:**
- Modify: `inference/ClusterCoordinator.py`
- Test: `test/p2p/test_cluster_replan_flow.py`

**Step 1: Write the failing test**

```python
from inference.ClusterCoordinator import ClusterCoordinator
from inference.ClusterTypes import NodeProfile


def test_coordinator_can_replan_from_current_plan_when_profiles_change():
    coordinator = ClusterCoordinator(
        transport=None,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    initial_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    coordinator.current_plan = coordinator.build_plan(initial_profiles)

    changed_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 4, 10, loaded_shards=[]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 30, loaded_shards=[(12, 24)]),
    ]

    new_plan = coordinator.replan(changed_profiles)

    assert [(a.node_id, a.start_layer, a.end_layer) for a in new_plan.assignments] == [
        ("node-b", 0, 12),
        ("node-a", 12, 24),
    ]
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py::test_coordinator_can_replan_from_current_plan_when_profiles_change
```

Expected: FAIL because `ClusterCoordinator.replan(...)` and/or `current_plan` orchestration is missing.

**Step 3: Write minimal implementation**

```python
# inference/ClusterCoordinator.py
from inference.ClusterPlanner import plan_static_distribution, replan_distribution


class ClusterCoordinator:
    def __init__(...):
        ...
        self.current_plan = None

    def build_plan(self, profiles) -> PlacementPlan:
        ...
        plan = plan_static_distribution(...)
        self.current_plan = plan
        return plan

    def replan(self, profiles) -> PlacementPlan:
        normalized_profiles = [...]  # same normalization as build_plan
        if self.current_plan is None:
            self.current_plan = plan_static_distribution(...)
            return self.current_plan
        self.current_plan = replan_distribution(
            current=self.current_plan,
            nodes=normalized_profiles,
            total_layers=self.total_layers,
        )
        return self.current_plan
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py::test_coordinator_can_replan_from_current_plan_when_profiles_change
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_cluster_replan_flow.py
git commit -m "feat: add coordinator replanning state"
```

---

### Task 2: Prove the coordinator keeps the old plan when nothing important changed

**Files:**
- Modify: `test/p2p/test_cluster_replan_flow.py`
- Modify: `inference/ClusterCoordinator.py` (only if needed)

**Step 1: Write the failing test**

```python
def test_coordinator_replan_keeps_current_plan_when_shards_still_fit():
    coordinator = ClusterCoordinator(
        transport=None,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    initial = coordinator.build_plan(profiles)

    replanned = coordinator.replan(profiles)

    assert replanned is initial
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py::test_coordinator_replan_keeps_current_plan_when_shards_still_fit
```

Expected: FAIL if `replan(...)` returns a different object or recomputes unnecessarily.

**Step 3: Write minimal implementation**

If needed, preserve the exact `PlacementPlan` object when `replan_distribution(...)` returns `current` unchanged.

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py::test_coordinator_replan_keeps_current_plan_when_shards_still_fit
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_cluster_replan_flow.py
git commit -m "test: preserve current placement when replanning is unnecessary"
```

---

### Task 3: Cover join/leave/capacity changes at the coordinator layer

**Files:**
- Modify: `test/p2p/test_cluster_replan_flow.py`
- Modify: `inference/ClusterCoordinator.py` (only if needed)

**Step 1: Write the failing tests**

```python
def test_coordinator_replan_handles_node_leave_by_falling_back_to_static():
    coordinator = ClusterCoordinator(...)
    initial_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    coordinator.build_plan(initial_profiles)

    remaining = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
    ]

    new_plan = coordinator.replan(remaining)

    assert [(a.node_id, a.start_layer, a.end_layer) for a in new_plan.assignments] == [
        ("node-a", 0, 24),
    ]


def test_coordinator_replan_handles_node_join_without_forcing_move_when_current_plan_still_fits():
    coordinator = ClusterCoordinator(...)
    initial_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    current = coordinator.build_plan(initial_profiles)

    expanded_profiles = initial_profiles + [
        NodeProfile("node-c", "10.0.0.3", "cpu", 32, 26, 20, loaded_shards=[]),
    ]

    new_plan = coordinator.replan(expanded_profiles)

    assert new_plan is current
```

**Step 2: Run tests to verify they fail**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py -v
```

Expected: at least one FAIL showing current coordinator behavior is incomplete.

**Step 3: Write minimal implementation**

Only if needed, adjust `ClusterCoordinator.replan(...)` so it always normalizes profiles, calls `replan_distribution(...)`, and preserves `self.current_plan` consistently.

**Step 4: Run tests to verify they pass**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_cluster_replan_flow.py
git commit -m "test: cover coordinator join leave and capacity replans"
```

---

## Phase 2B — Validate the simple cost model and document its limits

### Task 4: Make move-cost assumptions explicit in tests and docs

**Files:**
- Modify: `test/p2p/test_sticky_replanner.py`
- Modify: `inference/README.md`
- Modify: `docs/README.md`
- Modify: `docs/diary/2026-03-25.md`

**Step 1: Write the failing test**

```python
def test_move_cost_penalizes_wider_shards_more_than_narrower_shards():
    assert score_assignment_move_cost("node-a", "node-b", (0, 16)) > score_assignment_move_cost(
        "node-a", "node-b", (0, 8)
    )
```

**Step 2: Run test to verify it fails if behavior is absent**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_move_cost_penalizes_wider_shards_more_than_narrower_shards
```

Expected: PASS if already covered; if so, keep the test as documentation value and continue.

**Step 3: Write minimal implementation**

Only if the test fails, adjust `score_assignment_move_cost(...)` minimally.

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py
```

Expected: PASS

**Step 5: Update docs**

Add short, explicit notes that the current cost model is intentionally temporary and list the next inputs to add later:
- node/device quality
- shard parameter size
- bandwidth
- loaded-state reuse
- CPU/GPU type
- load/unload time
- first/last shard special roles

**Step 6: Commit**

```bash
git add test/p2p/test_sticky_replanner.py inference/README.md docs/README.md docs/diary/2026-03-25.md
git commit -m "docs: clarify current move-cost heuristic and next inputs"
```

---

## Phase 2C — Upgrade from the simple heuristic to a smarter cost model

### Task 5: Introduce structured cost inputs instead of a bare shard-width penalty

**Files:**
- Modify: `inference/ClusterTypes.py`
- Modify: `inference/ClusterPlanner.py`
- Test: `test/p2p/test_sticky_replanner.py`

**Step 1: Write the failing test**

```python
from inference.ClusterPlanner import estimate_assignment_cost
from inference.ClusterTypes import NodeProfile, ShardAssignment


def test_estimate_assignment_cost_prefers_loaded_shard_on_same_gpu_capable_node():
    node = NodeProfile(
        "node-a", "10.0.0.1", "cuda", 64.0, 40.0, 200.0, loaded_shards=[(0, 12)]
    )
    assignment = ShardAssignment("node-a", 0, 12, role="first")

    cost_loaded = estimate_assignment_cost(node=node, assignment=assignment, shard_param_gb=8.0)

    cold_node = NodeProfile(
        "node-b", "10.0.0.2", "cpu", 64.0, 40.0, 40.0, loaded_shards=[]
    )
    cold_assignment = ShardAssignment("node-b", 0, 12, role="first")
    cost_cold = estimate_assignment_cost(node=cold_node, assignment=cold_assignment, shard_param_gb=8.0)

    assert cost_loaded < cost_cold
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_estimate_assignment_cost_prefers_loaded_shard_on_same_gpu_capable_node
```

Expected: FAIL because `estimate_assignment_cost(...)` and any supporting fields do not exist.

**Step 3: Write minimal implementation**

```python
# inference/ClusterPlanner.py
ROLE_PENALTY = {"first": 3.0, "middle": 0.0, "last": 3.0}
DEVICE_BONUS = {"cuda": -5.0, "cpu": 0.0}


def estimate_assignment_cost(
    node: NodeProfile,
    assignment: ShardAssignment,
    shard_param_gb: float,
    bandwidth_gbps: float | None = None,
) -> float:
    shard_key = (assignment.start_layer, assignment.end_layer)
    load_penalty = 0.0 if shard_key in node.loaded_shards else shard_param_gb
    device_penalty = DEVICE_BONUS.get(node.device, 0.0)
    role_penalty = ROLE_PENALTY.get(assignment.role, 0.0)
    bandwidth_penalty = 0.0 if bandwidth_gbps is None else 1.0 / max(bandwidth_gbps, 0.1)
    return load_penalty + role_penalty + bandwidth_penalty - node.compute_score * 0.01 + device_penalty
```

Keep this minimal. The point is to make the inputs explicit, not to invent a perfect model.

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_estimate_assignment_cost_prefers_loaded_shard_on_same_gpu_capable_node
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterTypes.py inference/ClusterPlanner.py test/p2p/test_sticky_replanner.py
git commit -m "feat: add structured assignment cost estimation"
```

---

### Task 6: Use the smarter cost model to choose between candidate owners

**Files:**
- Modify: `inference/ClusterPlanner.py`
- Modify: `test/p2p/test_sticky_replanner.py`

**Step 1: Write the failing test**

```python
def test_replanner_prefers_lower_cost_owner_instead_of_static_sort_order():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )
    nodes = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32.0, 4.0, 10.0, loaded_shards=[]),
        NodeProfile("node-b", "10.0.0.2", "cuda", 64.0, 40.0, 200.0, loaded_shards=[(12, 24)]),
        NodeProfile("node-c", "10.0.0.3", "cpu", 32.0, 30.0, 20.0, loaded_shards=[(0, 12)]),
    ]

    new_plan = replan_distribution(current=current, nodes=nodes, total_layers=24)

    assert [(a.node_id, a.start_layer, a.end_layer) for a in new_plan.assignments] == [
        ("node-c", 0, 12),
        ("node-b", 12, 24),
    ]
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_replanner_prefers_lower_cost_owner_instead_of_static_sort_order
```

Expected: FAIL because current `replan_distribution(...)` only keeps-or-falls-back.

**Step 3: Write minimal implementation**

Refactor `replan_distribution(...)` so it:
- preserves current assignments if all can stay
- otherwise replans each shard range against candidate nodes using the smarter cost function
- keeps layer boundaries identical to the current plan in this task
- sets `source_node_id` when ownership changes

Skeleton:

```python
def replan_distribution(current: PlacementPlan, nodes: list[NodeProfile], total_layers: int) -> PlacementPlan:
    ...
    if all(_assignment_can_stay(...)):
        return current

    new_assignments = []
    for assignment in current.assignments:
        best_node = min(
            nodes,
            key=lambda node: estimate_assignment_cost(
                node=node,
                assignment=ShardAssignment(
                    node_id=node.node_id,
                    start_layer=assignment.start_layer,
                    end_layer=assignment.end_layer,
                    role=assignment.role,
                ),
                shard_param_gb=float(assignment.num_layers),
            ),
        )
        source = None if best_node.node_id == assignment.node_id else assignment.node_id
        new_assignments.append(
            ShardAssignment(
                node_id=best_node.node_id,
                start_layer=assignment.start_layer,
                end_layer=assignment.end_layer,
                role=assignment.role,
                source_node_id=source,
            )
        )
    return PlacementPlan(model_id=current.model_id, assignments=new_assignments, coordinator_id=choose_coordinator(nodes))
```

Do not add `move/load/unload` execution here yet. Just produce a smarter plan.

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterPlanner.py test/p2p/test_sticky_replanner.py
git commit -m "feat: use smarter cost model for shard replanning"
```

---

### Task 7: Add an explicit replan diff helper

**Files:**
- Modify: `inference/ClusterPlanner.py`
- Create: `test/p2p/test_cluster_replan_flow.py`

**Step 1: Write the failing test**

```python
from inference.ClusterPlanner import diff_assignment_changes


def test_diff_assignment_changes_marks_keep_and_move():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )
    new = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        assignments=[
            ShardAssignment("node-c", 0, 12, role="first", source_node_id="node-a"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )

    diff = diff_assignment_changes(current, new)

    assert diff[0]["action"] == "move"
    assert diff[0]["from_node"] == "node-a"
    assert diff[0]["to_node"] == "node-c"
    assert diff[1]["action"] == "keep"
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py::test_diff_assignment_changes_marks_keep_and_move
```

Expected: FAIL because helper does not exist.

**Step 3: Write minimal implementation**

```python
def diff_assignment_changes(current: PlacementPlan, new: PlacementPlan) -> list[dict]:
    current_by_range = {(a.start_layer, a.end_layer): a for a in current.assignments}
    changes = []
    for assignment in new.assignments:
        key = (assignment.start_layer, assignment.end_layer)
        old = current_by_range[key]
        if old.node_id == assignment.node_id:
            action = "keep"
            from_node = old.node_id
        else:
            action = "move"
            from_node = old.node_id
        changes.append(
            {
                "action": action,
                "from_node": from_node,
                "to_node": assignment.node_id,
                "range": key,
                "role": assignment.role,
            }
        )
    return changes
```

**Step 4: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py::test_diff_assignment_changes_marks_keep_and_move
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/ClusterPlanner.py test/p2p/test_cluster_replan_flow.py
git commit -m "feat: add replan diff helper"
```

---

## Phase 2D — Wire docs and exports after the smarter model lands

### Task 8: Export the new planner API and document the smarter model

**Files:**
- Modify: `inference/__init__.py`
- Modify: `inference/README.md`
- Modify: `docs/README.md`
- Modify: `docs/diary/2026-03-25.md`

**Step 1: Write the failing import test**

```python
from inference import estimate_assignment_cost, diff_assignment_changes


def test_phase2_exports_are_available():
    assert callable(estimate_assignment_cost)
    assert callable(diff_assignment_changes)
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py::test_phase2_exports_are_available
```

Expected: FAIL because exports are missing.

**Step 3: Write minimal implementation**

Update `inference/__init__.py` to export:
- `estimate_assignment_cost`
- `diff_assignment_changes`

Update docs to explain:
- current coordinator replan flow
- current cost inputs
- what is still heuristic
- what remains unimplemented (`move/load/unload` execution, real transport ACK)

**Step 4: Run tests to verify they pass**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py test/p2p/test_cluster_replan_flow.py
```

Expected: PASS

**Step 5: Commit**

```bash
git add inference/__init__.py inference/README.md docs/README.md docs/diary/2026-03-25.md test/p2p/test_sticky_replanner.py test/p2p/test_cluster_replan_flow.py
git commit -m "docs: record smarter phase2 replanning flow"
```

---

## Final verification sequence

Run all relevant tests at the end:

```bash
python3.11 -m compileall inference/ClusterPlanner.py inference/ClusterCoordinator.py inference/__init__.py test/p2p/test_sticky_replanner.py test/p2p/test_cluster_replan_flow.py
python3.11 -m pytest -q test/p2p/test_sticky_replanner.py test/p2p/test_cluster_replan_flow.py test/p2p/test_cluster_coordinator.py test/p2p/test_static_cluster_planner.py test/p2p/test_device_profile.py test/p2p/test_remote_distributed_engine.py
python3.11 -m pytest -q test/inference/test_distributed_engine.py test/inference/test_inference_engine.py test/inference/test_model_registry.py
python3.11 scripts/cluster_planner_demo.py
```

Expected:
- all targeted p2p/control-plane tests PASS
- inference regressions PASS
- demo script still prints the expected scenarios

---

## Expected end state

After this plan is complete:
- `ClusterCoordinator` can replan from the current placement when node profiles change
- join / leave / capacity-degrade scenarios are tested at the control-plane level
- the planner no longer stops at keep-or-fallback only
- the cost model becomes explicit and extensible
- `source_node_id` starts carrying migration provenance
- README and diary clearly separate current implemented behavior from future work
- true transport execution is still a later step, but the planner/control-plane contract is ready for it
