# Inference Test Group

## What this module is

`test/inference/` validates shard loading, model registry behavior, local inference, distributed execution simulation, runtime lifecycle transitions, and demo-level execution flows tied to the `inference/` module.

## Directory structure

Representative tests include:

```text
test/inference/
├── test_delivery_demo.py
├── test_distributed_engine.py
├── test_inference_engine.py
├── test_local_demo.py
├── test_model_registry.py
├── test_runtime_lifecycle.py
├── test_runtime_lifecycle_demo.py
├── test_shard_config.py
└── test_shard_loader.py
```

## Key responsibilities / boundaries

This group covers:
- shard configuration and model-registry behavior,
- shard loading and execution engines,
- local and simulated distributed inference flows,
- runtime lifecycle behavior such as prepare/commit/drain/release.

It does **not** fully validate real peer transport; for that, see [`../p2p/README.md`](../p2p/README.md).

## How to run / verify

```bash
python3.11 -m pytest -q --tb=short test/inference
python3.11 scripts/local_demo.py
python3.11 scripts/runtime_lifecycle_demo.py
```

Some tests depend on local model assets. Llama-family cases may require a valid Hugging Face token or a local snapshot path.

## Dependencies / related modules

- Execution module: [`../../inference/README.md`](../../inference/README.md)
- Model metadata/paths: [`../../models/README.md`](../../models/README.md)
- Demo entrypoints: [`../../scripts/README.md`](../../scripts/README.md)

## Current status / limitations

This is one of the primary test groups in the repo, but some cases are environment-sensitive because model assets and device/runtime conditions matter. Treat it as strong module coverage, not a guarantee that every host can run every test unchanged.
