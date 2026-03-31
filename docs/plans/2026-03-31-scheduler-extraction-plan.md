# Scheduler Extraction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract scheduling/orchestration concerns into a top-level `scheduler/` module, migrate non-transport tests out of `test/p2p/`, and expose clear test entrypoints in the root Makefile.

**Architecture:** Keep `inference/` focused on shard loading, local execution, and runtime lifecycle. Create a sibling top-level `scheduler/` package for node inventory, cluster planning, replanning, coordinator logic, and associated metrics/types. Keep `p2p/` untouched and retain only true transport tests under `test/p2p/`.

**Tech Stack:** Python 3.11, pytest, existing `inference/`, `p2p/`, `test/` layout, root `Makefile`, Markdown docs.

---

## Scope and non-goals

This refactor is about boundary clarity, not new behavior. The scheduler extraction must preserve current planner, coordinator, and runtime interactions. Do not redesign algorithms, do not change `p2p/` transport code, and do not rename files to a new naming style during this pass. File names stay in current CamelCase / `test_*.py` style; only directories and imports change.

## Target package layout

```text
scheduler/
  __init__.py
  ClusterTypes.py
  ClusterPlanner.py
  ClusterCoordinator.py
  NodeInventory.py
  DeviceProfile.py

inference/
  ShardLoader.py
  ShardRegistry.py
  NodeRuntime.py
  InferenceEngine.py
  DistributedEngine.py
  ...

test/
  inference/
  scheduler/
  p2p/
    test_p2p.py
```

## Target test mapping

### Move from `test/p2p/` to `test/scheduler/`
- `test_cluster_coordinator.py`
- `test_cluster_planner_demo.py`
- `test_cluster_replan_flow.py`
- `test_coordinator_metrics.py`
- `test_device_profile.py`
- `test_dp_cluster_planner.py`
- `test_node_inventory.py`
- `test_scan_local_hardware.py`
- `test_static_cluster_planner.py`
- `test_sticky_replanner.py`

### Move from `test/p2p/` to `test/inference/`
- `test_runtime_lifecycle.py`

### Keep in `test/p2p/`
- `test_p2p.py`

## Compatibility strategy

For the first refactor pass, keep thin compatibility wrappers in the old `inference/*.py` scheduler-adjacent files. Each old file should re-export from the new `scheduler/` module so existing imports do not all need to change at once. After tests and docs stabilize, a later cleanup pass can remove those wrappers.

---

### Task 1: Create the `scheduler/` package skeleton

**Files:**
- Create: `/Users/acropolis/Github_Project/Paramind/scheduler/__init__.py`
- Create: `/Users/acropolis/Github_Project/Paramind/scheduler/ClusterTypes.py`
- Create: `/Users/acropolis/Github_Project/Paramind/scheduler/ClusterPlanner.py`
- Create: `/Users/acropolis/Github_Project/Paramind/scheduler/ClusterCoordinator.py`
- Create: `/Users/acropolis/Github_Project/Paramind/scheduler/NodeInventory.py`
- Create: `/Users/acropolis/Github_Project/Paramind/scheduler/DeviceProfile.py`

**Step 1: Write the failing test**

Use an existing scheduler-related test as the first red signal. Copy one simple import-based test path and update it to import from `scheduler.*` instead of `inference.*`.

Example target test to modify first:
```python
from scheduler.ClusterPlanner import plan_static_distribution
```

**Step 2: Run test to verify it fails**

Run:
```bash
python3.11 -m pytest -q --tb=short test/p2p/test_static_cluster_planner.py::test_static_distribution_assigns_contiguous_ranges
```

Expected: import failure for missing `scheduler` package.

**Step 3: Write minimal implementation**

Create the package directory and empty modules with minimal imports/exports so Python can resolve them.

**Step 4: Run test to verify import failure moves forward**

Run the same test again.

Expected: the error should move from “module not found” to missing symbols or wrong imports, proving the package is now discoverable.

**Step 5: Commit**

Do not commit yet in this session unless the user explicitly asks. Leave changes staged or unstaged until the full scheduler extraction is verified.

---

### Task 2: Move scheduler implementation into the new package

**Files:**
- Move/port: `/Users/acropolis/Github_Project/Paramind/inference/ClusterTypes.py` -> `/Users/acropolis/Github_Project/Paramind/scheduler/ClusterTypes.py`
- Move/port: `/Users/acropolis/Github_Project/Paramind/inference/ClusterPlanner.py` -> `/Users/acropolis/Github_Project/Paramind/scheduler/ClusterPlanner.py`
- Move/port: `/Users/acropolis/Github_Project/Paramind/inference/ClusterCoordinator.py` -> `/Users/acropolis/Github_Project/Paramind/scheduler/ClusterCoordinator.py`
- Move/port: `/Users/acropolis/Github_Project/Paramind/inference/NodeInventory.py` -> `/Users/acropolis/Github_Project/Paramind/scheduler/NodeInventory.py`
- Move/port: `/Users/acropolis/Github_Project/Paramind/inference/DeviceProfile.py` -> `/Users/acropolis/Github_Project/Paramind/scheduler/DeviceProfile.py`

**Step 1: Write the failing test**

Pick a small set of scheduler tests that cover planner/coordinator/inventory imports:
```bash
python3.11 -m pytest -q --tb=short \
  test/p2p/test_static_cluster_planner.py::test_static_distribution_assigns_contiguous_ranges \
  test/p2p/test_cluster_coordinator.py::test_coordinator_build_plan_accepts_profile_payloads \
  test/p2p/test_node_inventory.py::test_node_state_round_trip_preserves_new_fields
```

Before code migration, adjust these tests to import from `scheduler.*` and verify they fail.

**Step 2: Run test to verify it fails**

Expected: symbol/import failures until the new modules contain real code.

**Step 3: Write minimal implementation**

Copy or move the production code into the new `scheduler/` package, fixing intra-module imports so scheduler modules depend on each other through `scheduler.*`.

**Step 4: Add compatibility wrappers**

Rewrite these old files into thin re-export wrappers:
- `/Users/acropolis/Github_Project/Paramind/inference/ClusterTypes.py`
- `/Users/acropolis/Github_Project/Paramind/inference/ClusterPlanner.py`
- `/Users/acropolis/Github_Project/Paramind/inference/ClusterCoordinator.py`
- `/Users/acropolis/Github_Project/Paramind/inference/NodeInventory.py`
- `/Users/acropolis/Github_Project/Paramind/inference/DeviceProfile.py`

Example wrapper:
```python
from scheduler.ClusterPlanner import *  # noqa: F401,F403
```

**Step 5: Run test to verify it passes**

Run:
```bash
python3.11 -m pytest -q --tb=short \
  test/p2p/test_static_cluster_planner.py::test_static_distribution_assigns_contiguous_ranges \
  test/p2p/test_cluster_coordinator.py::test_coordinator_build_plan_accepts_profile_payloads \
  test/p2p/test_node_inventory.py::test_node_state_round_trip_preserves_new_fields
```

Expected: PASS.

---

### Task 3: Move scheduler tests out of `test/p2p/`

**Files:**
- Create directory: `/Users/acropolis/Github_Project/Paramind/test/scheduler/`
- Move files listed in the target mapping above from `test/p2p/` to `test/scheduler/`

**Step 1: Write the failing test**

Move one small test first, for example:
- `/Users/acropolis/Github_Project/Paramind/test/p2p/test_static_cluster_planner.py`
  -> `/Users/acropolis/Github_Project/Paramind/test/scheduler/test_static_cluster_planner.py`

Then run it from the new location.

**Step 2: Run test to verify it fails if imports/paths are stale**

Run:
```bash
python3.11 -m pytest -q --tb=short test/scheduler/test_static_cluster_planner.py
```

Expected: fail only if old import paths or path assumptions remain.

**Step 3: Write minimal implementation**

Fix imports and any path assumptions. Repeat for the rest of the scheduler test files.

**Step 4: Run test directory to verify it passes**

Run:
```bash
python3.11 -m pytest -q --tb=short test/scheduler
```

Expected: PASS for the migrated scheduler suite.

---

### Task 4: Move runtime lifecycle test into `test/inference/`

**Files:**
- Move: `/Users/acropolis/Github_Project/Paramind/test/p2p/test_runtime_lifecycle.py`
  -> `/Users/acropolis/Github_Project/Paramind/test/inference/test_runtime_lifecycle.py`

**Step 1: Write the failing test**

Move the file and run one focused test from the new location.

**Step 2: Run test to verify it fails if imports are stale**

Run:
```bash
python3.11 -m pytest -q --tb=short test/inference/test_runtime_lifecycle.py::test_shard_registry_tracks_lifecycle_states_by_range
```

**Step 3: Write minimal implementation**

Fix imports and any internal references so it runs from the new path.

**Step 4: Run test file to verify it passes**

Run:
```bash
python3.11 -m pytest -q --tb=short test/inference/test_runtime_lifecycle.py
```

Expected: PASS.

---

### Task 5: Update Makefile test entrypoints

**Files:**
- Modify: `/Users/acropolis/Github_Project/Paramind/Makefile`

**Desired targets:**
- Keep: `test-inference`
- Keep: `test-backend`
- Add: `test-scheduler`
- Add: `test-p2p`
- Keep: `local-demo`
- Remove: `p2p-demo`

**Step 1: Write the failing test**

Run the not-yet-existing target:
```bash
make test-scheduler
```

Expected: `No rule to make target 'test-scheduler'`.

**Step 2: Write minimal implementation**

Update `Makefile` so:
- `test-inference` runs `test/inference`
- `test-backend` runs `test/backend`
- `test-scheduler` runs `test/scheduler`
- `test-p2p` runs `test/p2p/test_p2p.py`
- `p2p-demo` is removed for now

Do **not** delete `test-inference` or `test-backend`; they are still useful and already aligned with the desired split.

**Step 3: Run Make targets to verify they pass**

Run:
```bash
make test-scheduler
make test-inference
```

Expected: scheduler and inference suites run through the intended directories.

---

### Task 6: Delete backup wrappers only after green verification

**Files:**
- Potentially delete thin wrappers in:
  - `/Users/acropolis/Github_Project/Paramind/inference/ClusterTypes.py`
  - `/Users/acropolis/Github_Project/Paramind/inference/ClusterPlanner.py`
  - `/Users/acropolis/Github_Project/Paramind/inference/ClusterCoordinator.py`
  - `/Users/acropolis/Github_Project/Paramind/inference/NodeInventory.py`
  - `/Users/acropolis/Github_Project/Paramind/inference/DeviceProfile.py`

**Rule:** only do this if all migrated tests pass and there are no remaining imports depending on the old locations.

**Step 1: Write the failing check**

Search for remaining imports:
```bash
rg -n "inference\.(ClusterTypes|ClusterPlanner|ClusterCoordinator|NodeInventory|DeviceProfile)" .
```

Expected: no remaining production/test imports before deletion.

**Step 2: Delete wrappers**

Only after the search is clean.

**Step 3: Run targeted verification again**

Run:
```bash
python3.11 -m pytest -q --tb=short test/scheduler test/inference/test_runtime_lifecycle.py
```

Expected: PASS.

---

### Task 7: Update docs and README navigation

**Files:**
- Modify: `/Users/acropolis/Github_Project/Paramind/README.md`
- Modify: `/Users/acropolis/Github_Project/Paramind/inference/README.md`
- Create or modify: `/Users/acropolis/Github_Project/Paramind/scheduler/README.md`
- Modify: `/Users/acropolis/Github_Project/Paramind/docs/diary/2026-03-31.md`

**Step 1: Write the failing check**

Manually verify docs do not yet describe the new three-way split:
- `scheduler/`
- `inference/`
- `p2p/`

**Step 2: Write minimal implementation**

Update docs to explain:
- `scheduler/` owns planning/orchestration
- `inference/` owns shard execution/runtime
- `p2p/` owns transport

All paths must remain relative in docs.

**Step 3: Run lightweight doc verification**

Run:
```bash
python3.11 - <<'PY'
from pathlib import Path
for path in [Path('README.md'), Path('inference/README.md'), Path('scheduler/README.md')]:
    text = path.read_text()
    assert '/Users/' not in text, path
print('docs-relative-ok')
PY
```

Expected: `docs-relative-ok`

---

### Task 8: Final verification

**Files:**
- No new files; verification only

**Step 1: Run compile check**

```bash
python3.11 -m compileall inference scheduler test
```

Expected: success.

**Step 2: Run targeted scheduler/inference suites**

```bash
python3.11 -m pytest -q --tb=short test/scheduler test/inference/test_runtime_lifecycle.py test/inference
```

Expected: all relevant suites pass, excluding known external-precondition tests if necessary.

**Step 3: Run Makefile entrypoints**

```bash
make test-scheduler
make test-inference
make test-p2p
```

**Step 4: Run Black check before completion**

```bash
uvx --from black black --check .
```

Expected: clean.

---

## Notes on `Makefile`

The current `Makefile` targets:

```make
test-inference:
	$(PYTHON) -m pytest test/inference -v

test-backend:
	$(PYTHON) -m pytest test/backend -v
```

should be **kept**, not removed. They are still valid and match the desired split. The only target that should be removed in this refactor is:

```make
p2p-demo:
	$(PYTHON) main.py
```

because it no longer reflects a maintained entrypoint.

## Notes on backups

If "backup" means compatibility wrappers or moved legacy files, delete them only after:
1. the migrated tests pass,
2. search confirms no remaining imports rely on old paths, and
3. Makefile / docs already point to the new structure.

Until then, keep them as temporary compatibility shims.
