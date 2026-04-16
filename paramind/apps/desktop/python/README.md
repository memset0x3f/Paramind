# Desktop Python Backend Layer

## What this module is

`paramind/apps/desktop/python/` contains the Python-side entry layer for the desktop application. It wires the FastAPI backend, local coordinator flows, runtime settings, and import bootstrapping needed to run the desktop app outside the Electron renderer.

## Directory structure

```text
python/
├── app/              # backend core modules and service layer
├── backend.py        # FastAPI backend entrypoint
├── main.py           # local coordinator app launcher
├── network_config.py # network/environment configuration helper
└── test_api.py       # small API-side development helper
```

## Key responsibilities / boundaries

This layer owns:
- starting the backend process,
- bootstrapping local Python import paths for the desktop runtime,
- connecting the desktop shell to the deeper service layer in `app/`.

It does **not** own the core service logic itself; that lives in [`app/README.md`](app/README.md).

## Key entrypoints or important files

- `backend.py`: main FastAPI backend entrypoint used by the desktop app
- `main.py`: coordinator/local service launcher path
- `network_config.py`: environment/network config helper
- `app/`: backend core logic and data model implementation

## How to run / verify

Typical development entrypoint:

```bash
cd /Users/acropolis/Github_Project/Paramind/paramind/apps/desktop
uv run --project . python python/backend.py
```

For module-specific behavior and backend service structure, continue to [`app/README.md`](app/README.md). For test coverage, see [`../../../../test/backend/README.md`](../../../../test/backend/README.md).

## Dependencies / related modules

- Backend core logic: [`app/README.md`](app/README.md)
- Desktop shell overview: [`../README.md`](../README.md)
- Backend tests: [`../../../../test/backend/README.md`](../../../../test/backend/README.md)

## Current status / limitations

This layer is intentionally thin. Most architectural understanding should come from the deeper `app/` package and the desktop top-level README, not from these launch wrappers alone.
