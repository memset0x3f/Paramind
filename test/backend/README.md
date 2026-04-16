# Backend Test Group

## What this module is

`test/backend/` covers the desktop application backend and frontend-adjacent integration surfaces. It focuses on backend API behavior, runtime selection, inference runner integration, coordinator transport glue, packaged-runtime assumptions, and the renderer/browser harness loop.

## Directory structure

Representative tests include:

```text
test/backend/
├── test_api.py
├── test_chat_state.mjs
├── test_coordinator_transport.py
├── test_desktop_packaging.mjs
├── test_inference_runner.py
├── test_packaged_runtime.py
├── test_renderer_harness.mjs
├── test_renderer_harness.spec.mjs
└── test_runtime_selection.py
```

## Key responsibilities / boundaries

This group covers:
- FastAPI backend payloads and routes,
- backend runtime selection and inference runner behavior,
- coordinator transport glue used by the desktop app,
- browser-harness and renderer-state checks tied to the desktop prototype.

It does **not** aim to be the main test home for top-level scheduler or inference logic; those live in their own test groups.

## How to run / verify

Common entrypoints:

```bash
python3.11 -m pytest -q --tb=short test/backend
make test-frontend-fast
make test-frontend-smoke
```

Use the fast frontend path first for renderer behavior. Use the smoke path when preload or backend startup behavior matters.

## Dependencies / related modules

- Desktop app overview: [`../../paramind/apps/desktop/README.md`](../../paramind/apps/desktop/README.md)
- Backend core: [`../../paramind/apps/desktop/python/app/README.md`](../../paramind/apps/desktop/python/app/README.md)
- Renderer: [`../../paramind/apps/desktop/renderer/README.md`](../../paramind/apps/desktop/renderer/README.md)

## Current status / limitations

This group mixes Python backend tests and JavaScript/browser harness tests because they validate one product surface. Some cases depend on local desktop tooling or Playwright availability, so not every test here is equally lightweight.
