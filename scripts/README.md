# Scripts Module

## What this module is

`scripts/` collects runnable demos and helper entrypoints that expose repository behavior from the command line. It is the best place to start when you want to observe the prototype before reading implementation details.

## Directory structure

```text
scripts/
├── cluster_planner_demo.py      # planner behavior under controlled membership changes
├── delivery_demo.py             # recommended overall prototype walkthrough
├── demo_rendering.py            # desktop rendering/demo support entrypoint
├── local_demo.py                # local execution and shard behavior demonstration
├── runtime_lifecycle_demo.py    # staged runtime ownership/lifecycle demonstration
└── scan_local_hardware.py       # local hardware/profile snapshot helper
```

## Key responsibilities / boundaries

This directory owns runnable entrypoints and demonstrations. It does **not** define the core logic of placement, runtime execution, or transport itself; those belong to sibling modules.

## Key entrypoints or important files

Recommended first entrypoint:
- `delivery_demo.py`: the best single walkthrough if you want one demo that ties multiple layers together.

Other scripts:
- `local_demo.py`: use when focusing on local inference and shard execution behavior
- `cluster_planner_demo.py`: use when focusing on planner and reconfiguration behavior
- `runtime_lifecycle_demo.py`: use when focusing on prepare/commit/draining semantics
- `scan_local_hardware.py`: use when examining local capability snapshots
- `demo_rendering.py`: helper path for renderer/demo work

## How to run / verify

Run scripts from the repo root unless a script says otherwise:

```bash
python3.11 scripts/delivery_demo.py
python3.11 scripts/local_demo.py
python3.11 scripts/cluster_planner_demo.py
python3.11 scripts/runtime_lifecycle_demo.py
python3.11 scripts/scan_local_hardware.py
```

## Dependencies / related modules

- Planner demos rely on [`../scheduler/README.md`](../scheduler/README.md)
- Execution demos rely on [`../inference/README.md`](../inference/README.md)
- Some end-to-end paths depend on [`../p2p/README.md`](../p2p/README.md)
- Test coverage is split across the `test/` subtree

## Current status / limitations

These scripts are demos and developer helpers, not production service entrypoints. Some are deterministic and controlled; others are closer to environment-dependent smoke tests. Use them to understand behavior, not to infer production readiness.
