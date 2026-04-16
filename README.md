# ParaMind

ParaMind is an experimental repository for distributed large-language-model inference on user-controlled peers. The repo is organized around four main lines of work: model execution, cluster planning, peer-to-peer transport, and a desktop application prototype that exposes the runtime as a chat-style session.

## Start here

- **Understand inference and shard runtime**: see [`inference/README.md`](inference/README.md)
- **Understand planning and reconfiguration**: see [`scheduler/README.md`](scheduler/README.md)
- **Understand the desktop app prototype**: see [`paramind/apps/desktop/README.md`](paramind/apps/desktop/README.md)
- **Run demos and developer entrypoints**: see [`scripts/README.md`](scripts/README.md)
- **Inspect test coverage by subsystem**: see the READMEs under [`test/`](test/)

## Architecture map

- [`common/`](common/README.md): shared concepts, configuration helpers, constants, and utility functions
- [`models/`](models/README.md): local model registry and model-profile generation
- [`inference/`](inference/README.md): shard loading, execution engines, and runtime shard lifecycle
- [`scheduler/`](scheduler/README.md): node profiling, placement, replanning, and coordinator-side orchestration
- [`p2p/`](p2p/README.md): peer discovery and transport-side communication primitives
- [`paramind/apps/desktop/`](paramind/apps/desktop/README.md): Electron shell, renderer, and FastAPI backend prototype
- [`scripts/`](scripts/README.md): demos and helper scripts that expose repo behavior from the command line
- [`test/`](test/backend/README.md): subsystem-specific test groups for backend, inference, scheduler, P2P, and shared code
- [`docs/`](docs/): project notes, plans, essays, and research artifacts

## Module index

### Core modules
- [`common/README.md`](common/README.md)
- [`models/README.md`](models/README.md)
- [`inference/README.md`](inference/README.md)
- [`scheduler/README.md`](scheduler/README.md)
- [`p2p/README.md`](p2p/README.md)

### Desktop application
- [`paramind/apps/desktop/README.md`](paramind/apps/desktop/README.md)
- [`paramind/apps/desktop/python/README.md`](paramind/apps/desktop/python/README.md)
- [`paramind/apps/desktop/python/app/README.md`](paramind/apps/desktop/python/app/README.md)
- [`paramind/apps/desktop/renderer/README.md`](paramind/apps/desktop/renderer/README.md)
- [`paramind/apps/desktop/scripts/README.md`](paramind/apps/desktop/scripts/README.md)

### Scripts and demos
- [`scripts/README.md`](scripts/README.md)

### Test groups
- [`test/backend/README.md`](test/backend/README.md)
- [`test/common/README.md`](test/common/README.md)
- [`test/inference/README.md`](test/inference/README.md)
- [`test/loader/README.md`](test/loader/README.md)
- [`test/p2p/README.md`](test/p2p/README.md)
- [`test/scheduler/README.md`](test/scheduler/README.md)

## Reading order

If you are new to the repo, the most productive reading order is:

1. [`README.md`](README.md) for module navigation
2. [`inference/README.md`](inference/README.md) and [`scheduler/README.md`](scheduler/README.md) for the core runtime split
3. [`p2p/README.md`](p2p/README.md) for transport responsibilities
4. [`paramind/apps/desktop/README.md`](paramind/apps/desktop/README.md) for the user-facing prototype
5. [`scripts/README.md`](scripts/README.md) if you want runnable demonstrations before reading more code

## Current status

This repository contains an active prototype rather than a finished product. Some modules are mature enough to guide development directly, while others are still experimental or optimized for local demos and research iteration. The READMEs in each module describe those boundaries explicitly so the root README can remain a navigation hub rather than a second copy of subsystem internals.
