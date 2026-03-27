# Placement Metric Rigor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current ad-hoc coordinator and capacity scoring with a documented, testable metric stack that shares one source of truth across planner, coordinator, demo, and local hardware discovery.

**Architecture:** Keep `NodeState` as the canonical node model and add rigor through derived metric helpers instead of introducing a second competing node-capability type. Make the coordinator metric explicitly bottleneck-oriented and plan-aware when a plan exists, with a cold-start reference-slice fallback when it does not. Add a hardware scan script that produces a real local `NodeState`/metric report and make the interactive demo import the same scoring helpers instead of duplicating formulas.

**Tech Stack:** Python 3.11, PyTorch, existing `inference/NodeInventory.py`, `inference/ClusterPlanner.py`, pytest.

---

## Design decisions frozen before execution

This plan bakes in five decisions so implementation does not drift:

1. **No new top-level `NodeCapability` core type.** `NodeState` remains canonical. If a narrower view is needed, it stays private or derived.
2. **Primary optimization objective:** estimated **bottleneck stage time**. Secondary terms may exist, but bottleneck latency is the one quantity all public explanations should point back to.
3. **Coordinator selection is plan-aware when possible, cold-start-reference when not.** If a `PlacementPlan` exists, coordinator scoring may use it. Otherwise it uses a documented reference slice width.
4. **`bytes_per_block` is an approximation, not a physically exact model.** The docs must say so explicitly.
5. **Demo and scripts must import metric helpers from production code.** No duplicate scoring formulas in the demo.

---

### Task 1: Document the metric model and freeze the vocabulary

**Files:**
- Modify: `docs/plans/2026-03-25-placement-metric-rigor.md`
- Modify: `inference/README.md`
- Modify: `docs/README.md`
- Modify: `docs/diary/2026-03-25.md`

**Step 1: Add the failing expectation in docs-review terms**

Write down the specific terminology that must become true after this task:
- `NodeState` is the canonical node model
- coordinator metric optimizes estimated bottleneck stage time
- `bytes_per_block` is a calibrated approximation
- demo imports coordinator scoring from production code

**Step 2: Update the plan text itself**

Revise this plan so it no longer proposes `NodeCapability` as a peer to `NodeState`. Replace it with language about derived metric helpers on top of `NodeState`.

**Step 3: Update project docs**

Add a short “placement metrics” subsection to:
- `inference/README.md`
- `docs/README.md`

The subsection must state:
- the primary objective is bottleneck stage time
- coordinator selection is plan-aware with cold-start fallback
- capacity currently uses a calibrated `bytes_per_block` approximation
- network terms are out of scope for the current LAN-oriented version

**Step 4: Update the diary**

Append a dated note explaining:
- why `NodeState` stayed canonical
- why “rigor” here means consistent objective + testability, not perfect hardware realism

**Step 5: Verify**

Run:
```bash
python3.11 - <<'PY'
from pathlib import Path
for path in [
    Path('inference/README.md'),
    Path('docs/README.md'),
    Path('docs/diary/2026-03-25.md'),
    Path('docs/plans/2026-03-25-placement-metric-rigor.md'),
]:
    text = path.read_text()
    assert 'bottleneck' in text.lower()
    assert 'nodestate' in text.lower() or 'NodeState' in text
print('docs-ok')
PY
```

Expected: `docs-ok`

---

### Task 2: Create a single coordinator metric source of truth

**Files:**
- Modify: `inference/ClusterPlanner.py`
- Modify: `inference/__init__.py`
- Test: `test/p2p/test_coordinator_metrics.py`

**Step 1: Write the failing tests**

Create `test/p2p/test_coordinator_metrics.py` with tests for:
- monotonicity: faster node => lower estimated stage time for same width
- monotonicity: more capacity => never worse reference coordinator score for same node speed
- deterministic coordinator choice on fixed synthetic nodes
- plan-aware coordinator score can differ from cold-start fallback when a plan is supplied

**Step 2: Run tests to verify they fail**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_coordinator_metrics.py
```

Expected: FAIL because helpers do not yet exist.

**Step 3: Add minimal public metric helpers**

In `inference/ClusterPlanner.py`, add documented helpers such as:
- `estimate_reference_stage_width(total_layers, active_nodes)`
- `estimate_stage_time_for_node(node, width, total_layers, role='middle')`
- `coordinator_score(node, total_layers, active_nodes, role='first', current_plan=None)`

Requirements:
- all helpers consume normalized `NodeState`
- the score is bottleneck-latency-oriented, not a raw product
- when `current_plan` exists, score may use the node’s actual assigned widths/roles
- when `current_plan` is absent, score falls back to a reference width

Keep `choose_coordinator(nodes)` signature stable, but allow an optional `current_plan=None` path internally if needed.

**Step 4: Export the public helper**

Expose the public scoring helper via `inference/__init__.py` so the demo can import it instead of duplicating logic.

**Step 5: Re-run tests**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_coordinator_metrics.py
```

Expected: PASS

---

### Task 3: Add a local hardware scan script that emits real node metrics

**Files:**
- Create: `scripts/scan_local_hardware.py`
- Test: `test/p2p/test_scan_local_hardware.py`
- Modify: `inference/NodeInventory.py` (only if a helper is missing)

**Step 1: Write the failing tests**

Create `test/p2p/test_scan_local_hardware.py` with tests for:
- rendering includes `node_id`, `device_type`, `free_memory_gb`, `effective_speed`, `effective_capacity_blocks`
- JSON mode returns a parseable object with those fields
- scanner uses `NodeInventory.from_local_snapshot(...)` or equivalent production helper rather than duplicating detection logic

**Step 2: Run tests to verify they fail**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_scan_local_hardware.py
```

Expected: FAIL because script/helper does not yet exist.

**Step 3: Implement the script**

Create `scripts/scan_local_hardware.py` with:
- default text output
- optional `--json`
- optional `--node-id`

The script must:
- call production `NodeInventory` / `DeviceProfile` helpers
- normalize into `NodeState`
- print:
  - `node_id`
  - `host`
  - `device_type`
  - `total_memory_gb`
  - `free_memory_gb`
  - `effective_speed`
  - `effective_capacity_blocks`
  - `loaded_ranges`

**Step 4: Re-run tests**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_scan_local_hardware.py
python3.11 scripts/scan_local_hardware.py --json
```

Expected:
- tests PASS
- JSON output contains the required keys

---

### Task 4: Make the interactive demo import the shared coordinator metric

**Files:**
- Modify: `scripts/cluster_planner_demo.py`
- Modify: `test/p2p/test_cluster_planner_demo.py`

**Step 1: Write or update failing tests**

Add demo tests that assert:
- scoring output includes the named production metric
- demo does not print a hand-rolled `quality_score = speed * capacity` formula
- demo still supports `start`, `join`, `drop`, `show`, `quit`

**Step 2: Run tests to verify failure**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_planner_demo.py
```

Expected: FAIL because demo still owns its own scoring display.

**Step 3: Implement**

Refactor demo scoring so it imports the public coordinator metric helper from `ClusterPlanner.py`. The demo may still display explanatory breakdowns, but the final named score must come from production code.

**Step 4: Re-run tests**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_cluster_planner_demo.py
```

Expected: PASS

---

### Task 5: Replace implicit block capacity with a documented footprint approximation

**Files:**
- Modify: `inference/NodeInventory.py`
- Modify: `inference/ClusterPlanner.py`
- Test: `test/p2p/test_shard_footprint.py`

**Step 1: Write the failing tests**

Add tests for:
- `bytes_per_block` / `bytes_for_layers(...)` helper exists
- more available bytes => never lower capacity
- same node, larger shard footprint => no smaller stage-time denominator cheating
- docs-visible label says this is an approximation

**Step 2: Run tests to verify failure**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_shard_footprint.py
```

Expected: FAIL

**Step 3: Implement minimal footprint model**

Add a lightweight approximation helper such as:
- `bytes_per_block(model_id)`
- `bytes_for_layers(start, end, model_id)`

Wire capacity and migration-preload checks through the same approximation path.

**Step 4: Re-run tests**

Run:
```bash
python3.11 -m pytest -q test/p2p/test_shard_footprint.py
```

Expected: PASS

---

### Task 6: Run end-to-end regressions and sync docs to the implemented metric stack

**Files:**
- Modify: `inference/README.md`
- Modify: `docs/README.md`
- Modify: `docs/diary/2026-03-25.md`

**Step 1: Update docs with the final metric names**

Ensure docs now name the actual helpers and no longer refer to the old raw product as the active logic.

**Step 2: Run the relevant regressions**

Run:
```bash
python3.11 -m pytest -q test/p2p
python3.11 -m pytest -q test/inference/test_distributed_engine.py test/inference/test_inference_engine.py test/inference/test_model_registry.py
python3.11 -m py_compile inference/ClusterPlanner.py inference/NodeInventory.py scripts/cluster_planner_demo.py scripts/scan_local_hardware.py
```

Expected:
- p2p tests PASS (except any environment-gated skips)
- relevant inference regressions PASS
- `py_compile` PASS

---

## Out of scope

- full MILP placement
- thermal / power modeling
- production adaptive profiling loop
- wide-area network topology cost

## Success metrics

- coordinator selection and demo score share one production helper
- docs state exactly one primary optimization objective: bottleneck stage time
- local hardware scan script exists and reports real `NodeState`-derived metrics
- tests cover monotonicity, deterministic coordinator scenarios, and interactive demo behavior
