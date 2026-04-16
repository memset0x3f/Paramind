# ParaMind Desktop Renderer

## What this module is

`renderer/` contains the desktop app’s browser-side UI: HTML structure, visual styling, renderer-side state, interaction logic, and frontend harness helpers. It is responsible for presenting distributed runtime behavior as a chat-like session, not for implementing backend inference or planning.

## Directory structure

```text
renderer/
├── index.html          # main UI shell and layout
├── chat.js             # renderer state, rendering logic, and interaction flows
├── renderer.js         # Electron bridge wiring for renderer-side runtime hooks
├── dev_harness.html    # browser harness entrypoint
├── dev_harness.js      # harness behavior
└── harness_fixtures.js # renderer fixture states for fast frontend testing
```

## Key responsibilities / boundaries

This module owns:
- the visible chat/session interface,
- renderer-side state transitions,
- message list, sidebar, draft-card, and composer behavior,
- browser harness fixtures used for frontend-first testing.

It does **not** own:
- FastAPI backend logic,
- scheduler or inference policy,
- peer transport internals.

## Key entrypoints or important files

- `index.html`: structural layout and UI container IDs
- `chat.js`: main renderer logic and state machine
- `renderer.js`: Electron bridge integration
- `dev_harness.html` / `dev_harness.js`: fast browser-only validation path
- `harness_fixtures.js`: prebuilt renderer states for testing and demos

## How to run / verify

Preferred frontend-first loop from the repo root:

```bash
make test-frontend-fast
make open-frontend-harness
```

Use the Electron smoke path only when the change depends on preload behavior, backend bootstrap shape, or final integration:

```bash
make test-frontend-smoke
```

Related backend test guidance lives in [`../../../../test/backend/README.md`](../../../../test/backend/README.md).

## Dependencies / related modules

- Desktop app overview: [`../README.md`](../README.md)
- Backend service layer: [`../python/README.md`](../python/README.md)
- Backend test group: [`../../../../test/backend/README.md`](../../../../test/backend/README.md)

## Current status / limitations

The renderer is one of the most polished parts of the prototype, but it still targets demonstration and development workflows. Some flows depend on backend mock paths or harness fixtures, and the UI should be interpreted as a functional prototype rather than as a finalized product client.
