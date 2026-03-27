# Reconfiguration Diff and Orchestration Plan Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn planner output into actionable per-node reconfiguration instructions so coordinator/runtime can move from "new plan exists" to "each node knows what to keep, load, move, and unload."

**Architecture:** Keep `inference/ClusterPlanner.py` as the pure decision layer and add a second pure helper that compares old/new placement and emits explicit node actions. `inference/ClusterCoordinator.py` becomes the place that asks for a new plan, derives a `ReconfigurationPlan`, stores it, and can broadcast it. `inference/NodeRuntime.py` remains transport-facing and only consumes the actions intended for its node; this plan stops at action generation and routing, not full weight-transfer execution.

**Tech Stack:** Python 3.11, dataclasses, pytest, existing control-plane modules under `inference/` and `test/p2p/`.

---

## Scope and defaults

This plan deliberately focuses on **planner/coordinator semantics**, not real migration I/O. The action model is fixed up front so the implementer does not need to invent it:

- `keep`: node keeps serving a shard range it already owned
- `load`: node must newly load a shard range with no clear previous owner
- `move_in`: node will receive ownership of a shard range from another node
- `unload`: node should drop a shard range it no longer owns

The diff is **per node**, not global-only, because later orchestration needs to hand each runtime only its own actions. The planner keeps using `source_node_id` on `ShardAssignment`, but the new diff helper becomes the primary execution-facing artifact.

The current `build_plan(...)` and `replan(...)` APIs stay intact for compatibility. New APIs are additive.

---

### Task 1: Add explicit reconfiguration action types

**Files:**
- Modify: `inference/ClusterTypes.py`
- Test: `test/p2p/test_cluster_replan_flow.py`

**Step 1: Write the failing test**

```python
from inference.ClusterTypes import NodeReconfigurationAction, ReconfigurationPlan


def test_reconfiguration_action_round_trip_preserves_action_fields():
    action = NodeReconfigurationAction(
        node_id="node-c",
        action="move_in",
        start_layer=0,
        end_layer=12,
        role="first",
        from_node_id="node-a",
    )
    restored = NodeReconfigurationAction.from_dict(action.to_dict())

    assert restored == action


def test_reconfiguration_plan_groups_actions_by_node():
    plan = ReconfigurationPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        actions_by_node={
            "node-a": [NodeReconfigurationAction("node-a", "unload", 0, 12)],
            "node-c": [NodeReconfigurationAction("node-c", "move_in", 0, 12, from_node_id="node-a")],
        },
    )

    assert plan.actions_by_node["node-a"][0].action == "unload"
    assert plan.actions_by_node["node-c"][0].from_node_id == "node-a"
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py::test_reconfiguration_action_round_trip_preserves_action_fields test/p2p/test_cluster_replan_flow.py::test_reconfiguration_plan_groups_actions_by_node
```

Expected: FAIL because the new types do not exist.

**Step 3: Write minimal implementation**

Add to `inference/ClusterTypes.py`:

```python
@dataclass(frozen=True)
class NodeReconfigurationAction:
    node_id: str
    action: str
    start_layer: int
    end_layer: int
    role: str = "middle"
    from_node_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "action": self.action,
            "start_layer": self.start_layer,
            "end_layer": self.end_layer,
            "role": self.role,
            "from_node_id": self.from_node_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NodeReconfigurationAction":
        return cls(
            node_id=data["node_id"],
            action=data["action"],
            start_layer=data["start_layer"],
            end_layer=data["end_layer"],
            role=data.get("role", "middle"),
            from_node_id=data.get("from_node_id"),
        )


@dataclass(frozen=True)
class ReconfigurationPlan:
    model_id: str
    coordinator_id: str | None
    actions_by_node: dict[str, list[NodeReconfigurationAction]]
```

**Step 4: Run test to verify it passes**

Run the same pytest command again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/ClusterTypes.py test/p2p/test_cluster_replan_flow.py
git commit -m "feat: add reconfiguration action types"
```

---

### Task 2: Add a pure planner diff helper

**Files:**
- Modify: `inference/ClusterPlanner.py`
- Modify: `inference/__init__.py`
- Test: `test/p2p/test_cluster_replan_flow.py`

**Step 1: Write the failing test**

Add tests:

```python
from inference.ClusterPlanner import diff_assignment_changes
from inference.ClusterTypes import PlacementPlan, ShardAssignment


def test_diff_assignment_changes_marks_keep_move_in_and_unload():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )
    new = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-c",
        assignments=[
            ShardAssignment("node-c", 0, 12, role="first", source_node_id="node-a"),
            ShardAssignment("node-b", 12, 24, role="last"),
        ],
    )

    diff = diff_assignment_changes(current, new)

    assert diff["node-b"][0].action == "keep"
    assert diff["node-c"][0].action == "move_in"
    assert diff["node-c"][0].from_node_id == "node-a"
    assert diff["node-a"][0].action == "unload"


def test_diff_assignment_changes_marks_load_when_new_owner_has_no_source():
    current = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[ShardAssignment("node-a", 0, 24, role="first")],
    )
    new = PlacementPlan(
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        coordinator_id="node-a",
        assignments=[
            ShardAssignment("node-a", 0, 12, role="first"),
            ShardAssignment("node-c", 12, 24, role="last", source_node_id=None),
        ],
    )

    diff = diff_assignment_changes(current, new)

    assert diff["node-c"][0].action == "load"
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py::test_diff_assignment_changes_marks_keep_move_in_and_unload test/p2p/test_cluster_replan_flow.py::test_diff_assignment_changes_marks_load_when_new_owner_has_no_source
```

Expected: FAIL because `diff_assignment_changes` does not exist.

**Step 3: Write minimal implementation**

Add to `inference/ClusterPlanner.py`:

```python
def _assignment_key(assignment: ShardAssignment) -> tuple[int, int]:
    return (assignment.start_layer, assignment.end_layer)


def diff_assignment_changes(
    current: PlacementPlan,
    new: PlacementPlan,
) -> dict[str, list[NodeReconfigurationAction]]:
    current_by_key = {_assignment_key(a): a for a in current.assignments}
    new_by_key = {_assignment_key(a): a for a in new.assignments}
    actions: dict[str, list[NodeReconfigurationAction]] = {}

    for key, assignment in new_by_key.items():
        old = current_by_key.get(key)
        if old is not None and old.node_id == assignment.node_id:
            action = NodeReconfigurationAction(
                node_id=assignment.node_id,
                action="keep",
                start_layer=assignment.start_layer,
                end_layer=assignment.end_layer,
                role=assignment.role,
            )
            actions.setdefault(assignment.node_id, []).append(action)
            continue

        incoming_action = "move_in" if assignment.source_node_id is not None else "load"
        actions.setdefault(assignment.node_id, []).append(
            NodeReconfigurationAction(
                node_id=assignment.node_id,
                action=incoming_action,
                start_layer=assignment.start_layer,
                end_layer=assignment.end_layer,
                role=assignment.role,
                from_node_id=assignment.source_node_id,
            )
        )

    for key, assignment in current_by_key.items():
        new_assignment = new_by_key.get(key)
        if new_assignment is None or new_assignment.node_id != assignment.node_id:
            actions.setdefault(assignment.node_id, []).append(
                NodeReconfigurationAction(
                    node_id=assignment.node_id,
                    action="unload",
                    start_layer=assignment.start_layer,
                    end_layer=assignment.end_layer,
                    role=assignment.role,
                )
            )

    for node_id in actions:
        actions[node_id].sort(key=lambda item: (item.start_layer, item.end_layer, item.action))
    return actions
```

Export it from `inference/__init__.py`.

**Step 4: Run test to verify it passes**

Run the same pytest command again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/ClusterPlanner.py inference/__init__.py test/p2p/test_cluster_replan_flow.py
git commit -m "feat: add planner reconfiguration diff helper"
```

---

### Task 3: Add coordinator-level reconfiguration output

**Files:**
- Modify: `inference/ClusterCoordinator.py`
- Test: `test/p2p/test_cluster_replan_flow.py`

**Step 1: Write the failing test**

Add:

```python
from inference.ClusterTypes import ReconfigurationPlan


def test_coordinator_builds_reconfiguration_plan_after_replan():
    coordinator = ClusterCoordinator(
        transport=None,
        model_id="Qwen/Qwen2.5-0.5B-Instruct",
        total_layers=24,
    )
    initial_profiles = [
        NodeProfile("node-a", "10.0.0.1", "cpu", 32, 28, 30, loaded_shards=[(0, 12)]),
        NodeProfile("node-b", "10.0.0.2", "cpu", 32, 27, 29, loaded_shards=[(12, 24)]),
    ]
    coordinator.build_plan(initial_profiles)

    changed_profiles = initial_profiles + [
        NodeProfile("node-c", "10.0.0.3", "cuda", 64, 40, 80, loaded_shards=[]),
    ]

    reconfig = coordinator.build_reconfiguration(changed_profiles)

    assert isinstance(reconfig, ReconfigurationPlan)
    assert reconfig.model_id == coordinator.model_id
    assert "node-c" in reconfig.actions_by_node
    assert any(action.action in {"move_in", "load"} for action in reconfig.actions_by_node["node-c"])
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py::test_coordinator_builds_reconfiguration_plan_after_replan
```

Expected: FAIL because `build_reconfiguration` does not exist.

**Step 3: Write minimal implementation**

In `inference/ClusterCoordinator.py`:

```python
from inference.ClusterPlanner import diff_assignment_changes, plan_static_distribution, replan_distribution
from inference.ClusterTypes import ReconfigurationPlan

class ClusterCoordinator:
    ...
    def build_reconfiguration(self, profiles) -> ReconfigurationPlan:
        previous = self.current_plan
        if previous is None:
            new_plan = self.build_plan(profiles)
            actions = {
                assignment.node_id: [
                    NodeReconfigurationAction(
                        node_id=assignment.node_id,
                        action="load",
                        start_layer=assignment.start_layer,
                        end_layer=assignment.end_layer,
                        role=assignment.role,
                    )
                ]
                for assignment in new_plan.assignments
            }
            return ReconfigurationPlan(
                model_id=new_plan.model_id,
                coordinator_id=new_plan.coordinator_id,
                actions_by_node=actions,
            )

        new_plan = self.replan(profiles)
        actions = diff_assignment_changes(previous, new_plan)
        return ReconfigurationPlan(
            model_id=new_plan.model_id,
            coordinator_id=new_plan.coordinator_id,
            actions_by_node=actions,
        )
```

Store the latest `ReconfigurationPlan` on `self.last_reconfiguration`.

**Step 4: Run test to verify it passes**

Run the same pytest command again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py test/p2p/test_cluster_replan_flow.py
git commit -m "feat: add coordinator reconfiguration output"
```

---

### Task 4: Broadcast per-node reconfiguration payloads and runtime selection

**Files:**
- Modify: `inference/ClusterCoordinator.py`
- Modify: `inference/NodeRuntime.py`
- Test: `test/p2p/test_cluster_coordinator.py`

**Step 1: Write the failing test**

Add:

```python
def test_runtime_ignores_other_nodes_reconfiguration_actions_and_keeps_own_subset():
    transport = FakeTransport()
    runtime = NodeRuntime(node_id="node-b", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.attach_transport(transport)

    payload = {
        "model_id": "Qwen/Qwen2.5-0.5B-Instruct",
        "coordinator_id": "node-a",
        "actions_by_node": {
            "node-a": [{"node_id": "node-a", "action": "unload", "start_layer": 0, "end_layer": 12}],
            "node-b": [{"node_id": "node-b", "action": "keep", "start_layer": 12, "end_layer": 24, "role": "last"}],
        },
    }

    actions = runtime.handle_reconfiguration_plan(payload)

    assert len(actions) == 1
    assert actions[0].node_id == "node-b"
    assert actions[0].action == "keep"
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_coordinator.py::test_runtime_ignores_other_nodes_reconfiguration_actions_and_keeps_own_subset
```

Expected: FAIL because runtime cannot yet consume reconfiguration payloads.

**Step 3: Write minimal implementation**

Add to `inference/ClusterCoordinator.py`:

```python
def broadcast_reconfiguration(self, reconfiguration: ReconfigurationPlan):
    if self.transport is None:
        raise RuntimeError("No transport configured for cluster broadcasts")
    self.transport.broadcast(
        "cluster_reconfigure",
        {
            "model_id": reconfiguration.model_id,
            "coordinator_id": reconfiguration.coordinator_id,
            "actions_by_node": {
                node_id: [action.to_dict() for action in actions]
                for node_id, actions in reconfiguration.actions_by_node.items()
            },
        },
    )
```

Add to `inference/NodeRuntime.py`:

```python
def handle_reconfiguration_plan(self, payload: dict):
    raw_actions = payload.get("actions_by_node", {}).get(self.node_id, [])
    return [NodeReconfigurationAction.from_dict(item) for item in raw_actions]
```

Register `cluster_reconfigure` in `attach_transport(...)`.

Do **not** execute real loads/unloads yet; just parse and return the actions.

**Step 4: Run test to verify it passes**

Run the same pytest command again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/ClusterCoordinator.py inference/NodeRuntime.py test/p2p/test_cluster_coordinator.py
git commit -m "feat: broadcast and parse reconfiguration payloads"
```

---

### Task 5: Add coordinator-level action regressions for join and leave

**Files:**
- Modify: `test/p2p/test_cluster_replan_flow.py`
- Modify: `test/p2p/test_cluster_coordinator.py`

**Step 1: Write the failing tests**

Add one join test and one leave test:

```python
def test_join_reconfiguration_exposes_move_or_load_actions_for_new_node():
    ...
    reconfig = coordinator.build_reconfiguration(expanded_profiles)
    assert any(action.action in {"move_in", "load"} for action in reconfig.actions_by_node["node-c"])


def test_leave_reconfiguration_exposes_unload_for_departing_owner():
    ...
    reconfig = coordinator.build_reconfiguration(remaining_profiles)
    assert "node-b" in reconfig.actions_by_node
    assert any(action.action == "unload" for action in reconfig.actions_by_node["node-b"])
```

**Step 2: Run tests to verify they fail**

Run only the new tests. Expected: FAIL until action generation is correct for both join and leave.

**Step 3: Write minimal implementation**

If needed, adjust `diff_assignment_changes(...)` so:
- join produces `move_in` or `load` on the new node
- old owners get `unload`
- leave produces `unload` actions for departed ranges from old owners
- `keep` remains stable when ownership does not change

Keep the helper pure and deterministic.

**Step 4: Run tests to verify they pass**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py test/p2p/test_cluster_coordinator.py
```

Expected: PASS.

**Step 5: Commit**

```bash
git add test/p2p/test_cluster_replan_flow.py test/p2p/test_cluster_coordinator.py inference/ClusterPlanner.py
 git commit -m "test: cover join and leave reconfiguration actions"
```

---

### Task 6: Export and document the new execution-facing layer

**Files:**
- Modify: `inference/__init__.py`
- Modify: `inference/README.md`
- Modify: `docs/README.md`
- Modify: `docs/diary/2026-03-26.md` (create if missing; otherwise update current day's diary)

**Step 1: Write the failing test**

Add an export test near the planner/coordinator tests:

```python
from inference import diff_assignment_changes


def test_diff_assignment_changes_is_exported():
    assert callable(diff_assignment_changes)
```

**Step 2: Run test to verify it fails**

Run the single test. Expected: FAIL if export is missing.

**Step 3: Implement export and docs**

- Export `NodeReconfigurationAction`, `ReconfigurationPlan`, and `diff_assignment_changes`.
- Update `inference/README.md` to explain that the planner now emits an execution-facing reconfiguration diff.
- Update `docs/README.md` with the same current-state summary.
- Add a `2026-03-26` diary entry or append to today’s diary explaining:
  - why `source_node_id` alone was not enough
  - what the new action model is
  - that this still stops before real load/move/unload execution

**Step 4: Run final verification**

Run:
```bash
python3.11 -m py_compile inference/ClusterTypes.py inference/ClusterPlanner.py inference/ClusterCoordinator.py inference/NodeRuntime.py
python3.11 -m pytest -q test/p2p/test_cluster_replan_flow.py test/p2p/test_cluster_coordinator.py test/p2p/test_sticky_replanner.py test/p2p/test_static_cluster_planner.py
```

Expected: all PASS.

**Step 5: Commit**

```bash
git add inference/__init__.py inference/README.md docs/README.md docs/diary/2026-03-26.md
git commit -m "docs: document reconfiguration diff layer"
```

---

## Acceptance criteria

This plan is complete when all of the following are true:

1. The planner can derive explicit per-node actions from `current` and `new` placement.
2. The action vocabulary is fixed and documented: `keep`, `load`, `move_in`, `unload`.
3. The coordinator can emit a `ReconfigurationPlan` without breaking existing `build_plan(...)` / `replan(...)` callers.
4. The runtime can parse only the actions relevant to its own node from a broadcast payload.
5. Join and leave both produce action diffs that are test-covered.
6. Docs clearly say this is an orchestration precursor, not full migration execution.

## Assumptions and defaults

- This plan intentionally stops before real shard transfer. `move_in` is metadata, not yet a network copy.
- `unload` is also metadata only in this phase; it is not yet an enforced destructive operation.
- A moved shard is represented as `move_in` on the destination plus `unload` on the source. There is no separate `move_out` action in v1.
- The planner remains the single source of truth for old/new placement comparison; coordinator and runtime should not invent their own diff logic.
- If a new shard appears without a clear old owner, the destination action is `load`.
