# Inference Module

## What this module is

`inference/` owns shard materialization, local execution, distributed execution simulation, and runtime shard lifecycle management. It is the execution-side half of the core system split: the scheduler decides where shard ranges should live, while `inference/` turns those assignments into runnable shard objects and safe runtime state transitions.

## Directory structure

```text
inference/
├── __init__.py
├── DistributedEngine.py   # distributed simulation and remote-forward path
├── InferenceEngine.py     # single-node/local inference entrypoint
├── ModelRegistry.py       # model family and architecture metadata
├── ModelSplitter.py       # older full-model split path
├── NodeRuntime.py         # node-local action execution and request accounting
├── ShardConfig.py         # shard boundaries, roles, and dtype policy
├── ShardLoader.py         # lazy loading via meta shell + selective weights
└── ShardRegistry.py       # shard states, inflight tracking, and draining logic
```

## Key responsibilities / boundaries

This module owns:
- turning assigned contiguous layer ranges into executable shards,
- local and simulated distributed inference execution,
- runtime shard states such as `ready`, `serving`, and `draining`,
- request accounting needed for safe delayed unload.

This module does **not** own:
- placement or replanning policy (see [`../scheduler/README.md`](../scheduler/README.md)),
- model storage configuration itself (see [`../models/README.md`](../models/README.md)),
- peer membership or transport signaling (see [`../p2p/README.md`](../p2p/README.md)).

## Key entrypoints or important files

- `ShardLoader.py`: selective shard loading from model weights
- `ShardConfig.py`: contiguous range and shard-role definition
- `NodeRuntime.py`: prepare/commit/drain/release behavior at a node
- `ShardRegistry.py`: local truth for shard ownership and inflight state
- `InferenceEngine.py`: local generation entrypoint
- `DistributedEngine.py`: controlled distributed-stage simulation

## How to run / verify

Recommended verification paths:

```bash
python3.11 -m pytest -q --tb=short test/inference
python3.11 scripts/local_demo.py
python3.11 scripts/runtime_lifecycle_demo.py
```

See [`../test/inference/README.md`](../test/inference/README.md) for what those tests cover and which cases require extra model assets.

## Dependencies / related modules

- Planning and assignment source: [`../scheduler/README.md`](../scheduler/README.md)
- Model registry and byte-profile support: [`../models/README.md`](../models/README.md)
- Transport integration target: [`../p2p/README.md`](../p2p/README.md)
- Test coverage: [`../test/inference/README.md`](../test/inference/README.md)

## Current status / limitations

`inference/` is one of the core guidance modules in the repo and reflects the current runtime design. It still contains some older paths such as `ModelSplitter.py`, so not every file carries equal weight in the present architecture. The mainline design is lazy shard loading plus `NodeRuntime`/`ShardRegistry` lifecycle management.
