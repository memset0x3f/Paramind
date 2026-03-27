# Runtime Safety and Observability Demo Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add human-readable demo flows that show shard lifecycle execution, runtime safety, and reconfiguration state transitions clearly enough for a non-implementer to inspect the behavior from the terminal.

**Architecture:** Keep the production lifecycle logic in `inference/ShardRegistry.py`, `inference/NodeRuntime.py`, and `inference/ClusterCoordinator.py`, and build demos on top of those real objects rather than inventing a separate visualization-only model. Update the existing cluster planner demo so it explains planner decisions in the same session-friendly style, and add a new runtime lifecycle demo that walks through prepare, ready, commit, and release with explicit state tables and event logs.

**Tech Stack:** Python 3.11, existing inference control-plane modules, terminal text rendering, pytest, existing demo scripts under `scripts/`.

---

## Scope and defaults

This plan covers demo and observability output for runtime safety work inside `inference/`, not transport retries or peer-to-peer recovery logic in `p2p/`. The new lifecycle demo should exercise real `ClusterCoordinator`, `NodeRuntime`, and `ShardRegistry` behavior with fake loaders/transports, so the output reflects the actual control-plane semantics already implemented.

The output format should be stable, text-first, and easy to scan. Every demo should use consistent section headers and compact tables. Avoid ANSI color or fragile terminal dependencies in v1.

The required human-readable sections are:
- `PARAMETER GUIDE`
- `CURRENT CLUSTER`
- `EVENT LOG`
- `SHARD STATES`
- `ACTIONS`
- `READY / COMMIT STATUS`

The updated demos must use relative paths in docs and printed examples.

---

### Task 1: Add shared text formatting helpers for demos

**Files:**
- Create: `scripts/demo_rendering.py`
- Modify: `test/p2p/test_cluster_planner_demo.py`
- Create: `test/inference/test_runtime_lifecycle_demo.py`

**Step 1: Write the failing tests**

```python
from scripts.demo_rendering import render_key_value_block, render_table


def test_render_key_value_block_has_stable_header_and_alignment():
    rendered = render_key_value_block(
        "CURRENT CLUSTER",
        {
            "model": "Qwen/Qwen2.5-0.5B-Instruct",
            "coordinator": "node-a",
        },
    )

    assert "=== CURRENT CLUSTER ===" in rendered
    assert "model       : Qwen/Qwen2.5-0.5B-Instruct" in rendered
    assert "coordinator : node-a" in rendered


def test_render_table_prints_headers_and_rows_in_plain_text():
    rendered = render_table(
        "SHARD STATES",
        columns=["node", "shard", "state"],
        rows=[("node-a", "0-12", "serving")],
    )

    assert "[SHARD STATES]" in rendered
    assert "node" in rendered
    assert "0-12" in rendered
    assert "serving" in rendered
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/inference/test_runtime_lifecycle_demo.py::test_render_key_value_block_has_stable_header_and_alignment test/inference/test_runtime_lifecycle_demo.py::test_render_table_prints_headers_and_rows_in_plain_text
```

Expected: FAIL because `scripts/demo_rendering.py` does not exist.

**Step 3: Write minimal implementation**

Create `scripts/demo_rendering.py` with:
- `render_key_value_block(title, mapping)`
- `render_table(title, columns, rows)`
- simple deterministic text layout
- no terminal color dependencies

Prefer compact helpers such as:

```python
def render_key_value_block(title: str, mapping: dict[str, object]) -> str: ...
def render_table(title: str, columns: list[str], rows: list[tuple[object, ...]]) -> str: ...
def render_section(title: str, body: str) -> str: ...
```

**Step 4: Run test to verify it passes**

Run the same pytest command again. Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/demo_rendering.py test/inference/test_runtime_lifecycle_demo.py test/p2p/test_cluster_planner_demo.py
git commit -m "feat: add shared demo text rendering helpers"
```

---

### Task 2: Add a new runtime lifecycle demo script

**Files:**
- Create: `scripts/runtime_lifecycle_demo.py`
- Create: `test/inference/test_runtime_lifecycle_demo.py`

**Step 1: Write the failing test**

```python
import importlib


def test_runtime_lifecycle_demo_prints_prepare_and_commit_flow(capsys):
    demo = importlib.import_module("scripts.runtime_lifecycle_demo")

    exit_code = demo.main([])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "=== PARAMETER GUIDE ===" in output
    assert "=== CURRENT CLUSTER ===" in output
    assert "[EVENT LOG]" in output
    assert "cluster_reconfigure_prepare" in output
    assert "cluster_reconfigure_commit" in output
    assert "[SHARD STATES]" in output
    assert "serving" in output
    assert "pending_unload" in output
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q test/inference/test_runtime_lifecycle_demo.py::test_runtime_lifecycle_demo_prints_prepare_and_commit_flow
```

Expected: FAIL because the script does not exist.

**Step 3: Write minimal implementation**

Create `scripts/runtime_lifecycle_demo.py` that:
- builds a fake transport
- creates one coordinator and two runtimes
- seeds an initial serving shard on the old owner
- constructs a `ReconfigurationPlan` with `move_in` and `unload`
- runs `begin_reconfiguration(...)`
- prints formatted sections using `scripts/demo_rendering.py`

The script should expose:

```python
class DemoTransport: ...
class DemoLoader: ...
def render_runtime_state(...): ...
def run_demo() -> str: ...
def main(argv: list[str] | None = None) -> int: ...
```

**Step 4: Run test to verify it passes**

Run the same pytest command again. Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/runtime_lifecycle_demo.py test/inference/test_runtime_lifecycle_demo.py
git commit -m "feat: add runtime lifecycle demo script"
```

---

### Task 3: Make the runtime lifecycle demo show per-step state transitions clearly

**Files:**
- Modify: `scripts/runtime_lifecycle_demo.py`
- Modify: `test/inference/test_runtime_lifecycle_demo.py`

**Step 1: Write the failing test**

```python
def test_runtime_lifecycle_demo_shows_before_prepare_after_prepare_and_after_commit(capsys):
    demo = importlib.import_module("scripts.runtime_lifecycle_demo")

    demo.main([])
    output = capsys.readouterr().out

    assert "=== BEFORE RECONFIGURATION ===" in output
    assert "=== AFTER PREPARE ===" in output
    assert "=== AFTER COMMIT ===" in output
    assert "node-a" in output
    assert "node-c" in output
    assert "ready_nodes" in output
    assert "reconfiguration_committed" in output
```

**Step 2: Run test to verify it fails**

Run that single test. Expected: FAIL until phase snapshots are printed.

**Step 3: Write minimal implementation**

Extend the script so it prints three timeline snapshots:
- before `begin_reconfiguration(...)`
- immediately after prepare broadcast returns
- after commit broadcast completes

Each snapshot should include:
- node states
- shard registry rows
- ready/commit status
- event log lines so readers can follow causality

**Step 4: Run test to verify it passes**

Run the same test. Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/runtime_lifecycle_demo.py test/inference/test_runtime_lifecycle_demo.py
git commit -m "feat: add phase snapshots to runtime lifecycle demo"
```

---

### Task 4: Update the existing cluster planner demo to use the same human-readable format

**Files:**
- Modify: `scripts/cluster_planner_demo.py`
- Modify: `test/p2p/test_cluster_planner_demo.py`

**Step 1: Write the failing test**

```python
from scripts.cluster_planner_demo import ClusterDemoSession


def test_cluster_demo_uses_current_cluster_actions_and_event_log_sections():
    session = ClusterDemoSession()

    rendered = session.start(["node-a", "node-b", "node-c"])

    assert "=== CURRENT CLUSTER ===" in rendered
    assert "[ACTIONS]" in rendered
    assert "[EVENT LOG]" in rendered
    assert "[SHARD STATES]" in rendered
    assert "[SCORING]" in rendered
```

**Step 2: Run test to verify it fails**

Run that single test. Expected: FAIL until the output format is upgraded.

**Step 3: Write minimal implementation**

Refactor `scripts/cluster_planner_demo.py` so it imports `scripts/demo_rendering.py` and prints the same top-level structure as the lifecycle demo. Preserve existing command handling (`start`, `join`, `drop`, `show`, `help`, `quit`) but change rendering so users can clearly see:
- current active nodes
- planner metric inputs
- produced shard assignments
- latest command/event that caused the state change

**Step 4: Run test to verify it passes**

Run the same test. Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/cluster_planner_demo.py test/p2p/test_cluster_planner_demo.py
git commit -m "feat: improve cluster planner demo output"
```

---

### Task 5: Add a concise observability snapshot API for runtime/coordinator state

**Files:**
- Modify: `inference/NodeRuntime.py`
- Modify: `inference/ClusterCoordinator.py`
- Modify: `test/p2p/test_runtime_lifecycle.py`
- Modify: `test/p2p/test_cluster_coordinator.py`

**Step 1: Write the failing tests**

```python
def test_node_runtime_debug_snapshot_reports_registry_rows():
    runtime = NodeRuntime(node_id="node-a", family=ModelFamily.QWEN, total_layers=24, loader=FakeLoader())
    runtime.registry.put(ShardRecord((0, 12), role="first", state="serving", shard_obj="warm", active_owner=True))

    snapshot = runtime.debug_snapshot()

    assert snapshot["node_id"] == "node-a"
    assert snapshot["shards"][0]["state"] == "serving"


def test_cluster_coordinator_debug_snapshot_reports_pending_reconfiguration_state():
    coordinator = ClusterCoordinator(transport=FakeTransport(), model_id="Qwen/Qwen2.5-0.5B-Instruct", total_layers=24)

    snapshot = coordinator.debug_snapshot()

    assert snapshot["model_id"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert "reconfiguration_committed" in snapshot
```

**Step 2: Run tests to verify they fail**

Run the two tests. Expected: FAIL because `debug_snapshot()` does not exist.

**Step 3: Write minimal implementation**

Add:
- `NodeRuntime.debug_snapshot()` returning node id, assignment summary, registry rows, and maybe local shard presence
- `ClusterCoordinator.debug_snapshot()` returning model id, current coordinator, pending reconfiguration presence, ready nodes, reconfig ready nodes, and commit flag

Use these helpers in the demo scripts instead of manually peeking at internals.

**Step 4: Run tests to verify they pass**

Run the same tests again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/NodeRuntime.py inference/ClusterCoordinator.py test/p2p/test_runtime_lifecycle.py test/p2p/test_cluster_coordinator.py
git commit -m "feat: add runtime and coordinator debug snapshots"
```

---

### Task 6: Update the existing local demo so it points readers to the richer demos

**Files:**
- Modify: `scripts/local_demo.py`
- Modify: `test/inference/test_local_demo.py`

**Step 1: Write the failing test**

```python
def test_local_demo_mentions_runtime_and_cluster_demos_for_follow_up(monkeypatch, capsys):
    demo = importlib.import_module("scripts.local_demo")
    monkeypatch.setattr(demo, "LocalInferenceEngine", _FakeLocalEngine)
    monkeypatch.setattr(demo, "DistributedInferenceEngine", _FakeDistributedEngine)

    exit_code = demo.main(["--mode", "both", "--model-id", "Qwen/Qwen2.5-0.5B-Instruct", "--family", "qwen", "--device", "cpu", "--prompt", "Hello"]) 
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "See also: scripts/cluster_planner_demo.py" in output
    assert "See also: scripts/runtime_lifecycle_demo.py" in output
```

**Step 2: Run test to verify it fails**

Run that test. Expected: FAIL until follow-up hints are added.

**Step 3: Write minimal implementation**

Append a short “See also” block to `scripts/local_demo.py` output so users know which demo to run next for:
- planner placement behavior
- runtime lifecycle behavior

**Step 4: Run test to verify it passes**

Run the same test again. Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/local_demo.py test/inference/test_local_demo.py
git commit -m "docs: link richer demos from local demo"
```

---

### Task 7: Update docs for runtime safety demos and observability

**Files:**
- Modify: `inference/README.md`
- Modify: `docs/README.md`
- Modify: `docs/diary/2026-03-26.md`

**Step 1: Write the failing doc assertions**

```python
from pathlib import Path


def test_inference_readme_mentions_runtime_lifecycle_demo_and_debug_snapshots():
    text = Path("inference/README.md").read_text()
    assert "runtime_lifecycle_demo.py" in text
    assert "debug_snapshot" in text
    assert "cluster_reconfigure_prepare" in text
```

**Step 2: Run test to verify it fails**

Run a small pytest doc assertion or equivalent Python one-liner. Expected: FAIL until docs are updated.

**Step 3: Write minimal documentation updates**

Update the docs so they explain:
- what the new runtime lifecycle demo shows
- how the cluster demo and lifecycle demo differ
- what `debug_snapshot()` exposes
- that README path examples should use relative paths

**Step 4: Run test to verify it passes**

Run the same check again. Expected: PASS.

**Step 5: Commit**

```bash
git add inference/README.md docs/README.md docs/diary/2026-03-26.md
git commit -m "docs: document runtime safety demos and observability"
```

---

### Task 8: Final verification

**Files:**
- Verify: `scripts/runtime_lifecycle_demo.py`
- Verify: `scripts/cluster_planner_demo.py`
- Verify: `scripts/local_demo.py`
- Verify: `test/inference/test_runtime_lifecycle_demo.py`
- Verify: `test/p2p/test_cluster_planner_demo.py`

**Step 1: Run compile checks**

Run:
```bash
python3.11 -m py_compile scripts/demo_rendering.py scripts/runtime_lifecycle_demo.py scripts/cluster_planner_demo.py scripts/local_demo.py inference/NodeRuntime.py inference/ClusterCoordinator.py
```

Expected: PASS.

**Step 2: Run targeted tests**

Run:
```bash
python3.11 -m pytest -q --tb=short test/inference/test_runtime_lifecycle_demo.py test/inference/test_local_demo.py test/p2p/test_cluster_planner_demo.py test/p2p/test_runtime_lifecycle.py test/p2p/test_cluster_coordinator.py
```

Expected: all PASS.

**Step 3: Run both demos manually**

Run:
```bash
python3.11 scripts/runtime_lifecycle_demo.py
printf 'start node-a,node-b,node-c\njoin node-d\nshow\nquit\n' | python3.11 scripts/cluster_planner_demo.py
```

Expected:
- lifecycle demo prints `BEFORE RECONFIGURATION`, `AFTER PREPARE`, `AFTER COMMIT`
- cluster demo prints `CURRENT CLUSTER`, `EVENT LOG`, `ACTIONS`, `SHARD STATES`
- both outputs are understandable without reading code

**Step 4: Commit**

```bash
git add scripts/demo_rendering.py scripts/runtime_lifecycle_demo.py scripts/cluster_planner_demo.py scripts/local_demo.py inference/NodeRuntime.py inference/ClusterCoordinator.py inference/README.md docs/README.md docs/diary/2026-03-26.md test/inference/test_runtime_lifecycle_demo.py test/inference/test_local_demo.py test/p2p/test_cluster_planner_demo.py test/p2p/test_runtime_lifecycle.py test/p2p/test_cluster_coordinator.py
 git commit -m "feat: add runtime safety and observability demos"
```

---

## Acceptance criteria

1. There is a new demo script that shows runtime lifecycle execution through prepare and commit phases using real inference control-plane objects.
2. The existing cluster planner demo uses the same human-readable formatting style and surfaces event flow, actions, and shard states clearly.
3. Demo output is understandable to a human reading the terminal without needing to inspect source code.
4. Runtime and coordinator expose stable debug snapshot helpers that the demos can consume.
5. The local demo points users toward the richer planner and lifecycle demos.
6. `inference/README.md` is updated and uses relative paths when referring to files/scripts.

## Assumptions and defaults

- The demos are terminal-first and plain-text only in v1.
- Fake transport and fake loaders are acceptable in demos as long as they drive the real lifecycle semantics already implemented in `inference/`.
- This plan does not add retry logic, rollback logic, or peer-to-peer weight transfer.
- This plan does not change planner algorithms; it only improves runtime safety visibility and demo quality.
