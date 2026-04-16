# Desktop Backend Core (`app/`)

## What this module is

`paramind/apps/desktop/python/app/` contains the desktop backend’s core Python modules: configuration, persistence, coordinator integration, inference runner integration, and service-level orchestration. This is the main implementation layer behind the desktop app’s FastAPI backend.

## Directory structure

```text
app/
├── __init__.py
├── config.py              # backend settings and environment-derived config
├── coordinator.py         # localhost coordinator app and event state
├── coordinator_client.py  # client helper for coordinator interaction
├── database.py            # SQLite engine/session setup
├── inference.py           # inference runner and mock fallback path
├── mock_p2p.py            # lightweight desktop-local transport adapter
├── models.py              # SQLModel entities and persistence models
└── services.py            # DesktopAppService and orchestration logic
```

## Key responsibilities / boundaries

This package owns:
- backend settings and persistence setup,
- conversation, participant, peer, and event state handling,
- integration with inference runners and coordinator flows,
- service-level orchestration for bootstrap payloads, jobs, invitations, and event processing.

It does **not** own:
- renderer presentation logic (see [`../../renderer/README.md`](../../renderer/README.md)),
- top-level planning heuristics or shard-runtime internals at the repo root.

## Key entrypoints or important files

- `services.py`: main backend orchestration layer (`DesktopAppService`)
- `models.py`: persistent data model for peers, conversations, messages, jobs, and invitations
- `inference.py`: runtime selection and mock/real inference streaming bridge
- `coordinator.py`: local coordinator state and API/websocket interface
- `database.py`: SQLite engine/session setup
- `config.py`: backend settings model

## How to run / verify

The package is usually exercised through `python/backend.py`, but backend-focused tests live here conceptually:

```bash
python3.11 -m pytest -q --tb=short test/backend
```

Use [`../../../../../test/backend/README.md`](../../../../../test/backend/README.md) for test-group guidance.

## Dependencies / related modules

- Desktop backend entry layer: [`../README.md`](../README.md)
- Renderer/UI layer: [`../../renderer/README.md`](../../renderer/README.md)
- Core runtime modules at the repo root: inference, scheduler, and common

## Current status / limitations

This package is functional enough to back the desktop prototype, but some integration paths still rely on mock transport, local assumptions, or prototype packaging choices. Treat it as a real backend for a prototype app, not as a finished production service.
