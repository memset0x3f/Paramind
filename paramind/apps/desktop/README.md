# ParaMind Desktop App

## What this module is

`paramind/apps/desktop/` contains the current user-facing ParaMind prototype: an Electron shell, a renderer, and a Python backend that exposes chat-style distributed inference sessions. This directory is the most complete application surface in the repo, but it is still a prototype rather than a polished product.

## Directory structure

```text
paramind/apps/desktop/
├── assets/                 # application assets
├── docs/                   # app-specific notes and packaging docs
├── electron/               # Electron main-process code
├── python/                 # FastAPI backend and coordinator-side Python code
├── renderer/               # HTML/CSS/JS renderer UI
├── scripts/                # launch/build helper scripts
├── package.json            # Node/Electron package definition
└── playwright.config.mjs   # browser-test configuration
```

## Key responsibilities / boundaries

This directory owns:
- the desktop shell and renderer,
- the desktop-local FastAPI backend,
- local coordination between frontend state and backend behavior,
- packaging scripts and browser-based frontend harness support.

It does **not** own the repo’s core planning or inference logic in isolation; those are imported from top-level modules and adapted into the app.

## Key entrypoints or important files

- [`python/README.md`](python/README.md): backend entry layer and service startup
- [`python/app/README.md`](python/app/README.md): backend core modules
- [`renderer/README.md`](renderer/README.md): renderer structure and UI behavior
- [`scripts/README.md`](scripts/README.md): build and launch helpers
- `package.json`: Node-side scripts and packaging metadata

## How to run / verify

From this directory:

```bash
uv sync
npm ci
```

Common flows:

```bash
bash scripts/start_chat.sh
npm run start:chat:win
make test-frontend-fast
make open-frontend-harness
make test-frontend-smoke
```

Use the fast browser harness first when you are working on renderer behavior. Reserve the Electron smoke path for preload, backend bootstrapping, real inference startup, or final integration checks.

## Dependencies / related modules

- Backend details: [`python/README.md`](python/README.md)
- Renderer details: [`renderer/README.md`](renderer/README.md)
- Launch/build helpers: [`scripts/README.md`](scripts/README.md)
- Core inference and scheduling modules live at the repo root

## Current status / limitations

The desktop app is the most visible prototype surface, but it is not fully hardened. Packaging exists, frontend harnesses exist, and local multi-peer flows can be demonstrated, but production-level packaging, failure handling, and long-running distributed validation are still incomplete.
