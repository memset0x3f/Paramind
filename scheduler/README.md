# Scheduler Module

## What this module is

`scheduler/` owns node profiling, placement, membership-aware replanning, and coordinator-side orchestration. It is the control-plane half of the core runtime split: the scheduler decides which contiguous layer ranges should live on which nodes and how those assignments should transition when membership changes.

## Directory structure

```text
scheduler/
├── __init__.py
├── ClusterCoordinator.py   # build plan, broadcast, and reconfiguration phases
├── ClusterPlanner.py       # cold-start planning and join/drop/stable replanning
├── ClusterTypes.py         # node, assignment, placement, and reconfiguration types
├── DeviceProfile.py        # local profile helpers and compatibility layer
├── NodeInventory.py        # node-state construction and cluster snapshot support
└── model_profiles/         # planner-side model-profile artifacts
```

## Key responsibilities / boundaries

This module owns:
- node capability modeling,
- contiguous layer-range placement,
- branch-specific replanning for cold start, join, drop, and stable membership,
- derivation of reconfiguration actions,
- coordinator-side prepare/ready/commit orchestration.

This module does **not** own:
- local shard materialization or runtime draining semantics (see [`../inference/README.md`](../inference/README.md)),
- peer transport reliability (see [`../p2p/README.md`](../p2p/README.md)),
- desktop UI and session presentation.

## Key entrypoints or important files

- `ClusterPlanner.py`: `plan_static_distribution`, membership-aware replanning, diff helpers
- `ClusterCoordinator.py`: coordinator flow from plan generation to commit
- `ClusterTypes.py`: canonical planner/reconfiguration data structures
- `NodeInventory.py`: local node-state and inventory construction
- `DeviceProfile.py`: hardware/profile helper layer

## How to run / verify

Recommended verification paths:

```bash
python3.11 -m pytest -q --tb=short test/scheduler
python3.11 scripts/cluster_planner_demo.py
python3.11 scripts/scan_local_hardware.py
```

See [`../test/scheduler/README.md`](../test/scheduler/README.md) for coverage boundaries and planner-specific test groups.

## Dependencies / related modules

- Execution target: [`../inference/README.md`](../inference/README.md)
- Model path/profile support: [`../models/README.md`](../models/README.md)
- Shared concepts: [`../common/README.md`](../common/README.md)
- Test coverage: [`../test/scheduler/README.md`](../test/scheduler/README.md)

## Current status / limitations

`scheduler/` reflects the current cluster-control direction and is one of the core guidance modules in the repo. It still works with simplified capability models and byte-aware heuristics rather than a full end-to-end optimizer, so its README should be read as a description of current planner behavior, not a claim of globally optimal placement.
