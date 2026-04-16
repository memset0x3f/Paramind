# Scheduler Test Group

## What this module is

`test/scheduler/` validates node profiling, planner behavior, placement/replan flows, byte-aware planning helpers, and coordinator orchestration in the `scheduler/` module.

## Directory structure

Representative tests include:

```text
test/scheduler/
├── test_cluster_coordinator.py
├── test_cluster_planner_demo.py
├── test_cluster_replan_flow.py
├── test_device_profile.py
├── test_dp_cluster_planner.py
├── test_model_byte_planner.py
├── test_model_layer_sizes.py
├── test_model_resolve.py
├── test_node_inventory.py
├── test_scan_local_hardware.py
└── test_static_cluster_planner.py
```

## Key responsibilities / boundaries

This group covers:
- node inventory and device-profile construction,
- cold-start and membership-aware planning,
- byte-aware layer-range feasibility helpers,
- coordinator-side orchestration and planner demos.

It does **not** replace runtime execution tests or transport validation.

## How to run / verify

```bash
python3.11 -m pytest -q --tb=short test/scheduler
python3.11 scripts/cluster_planner_demo.py
python3.11 scripts/scan_local_hardware.py
```

## Dependencies / related modules

- Scheduler module: [`../../scheduler/README.md`](../../scheduler/README.md)
- Model profile inputs: [`../../models/README.md`](../../models/README.md)
- Demo scripts: [`../../scripts/README.md`](../../scripts/README.md)

## Current status / limitations

This is one of the primary guidance test groups in the repo. It is strongest for controlled planner behavior and orchestration logic, but it still inherits the simplifications of current capability models and does not replace full end-to-end distributed runtime validation.
