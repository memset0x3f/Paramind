# Desktop Scripts

## What this module is

`paramind/apps/desktop/scripts/` contains shell and PowerShell helpers for launching or packaging the desktop prototype. These scripts are convenience entrypoints for local development and packaging, not general-purpose infrastructure tooling.

## Directory structure

```text
scripts/
├── build-python.sh  # build the portable Python runtime used for packaging
├── start_chat.sh    # start local desktop chat peers on Unix-like systems
└── start_chat.ps1   # experimental Windows launcher
```

## Key responsibilities / boundaries

This directory owns launch/build convenience wrappers. It does **not** own desktop runtime logic, renderer behavior, or backend services.

## Key entrypoints or important files

- `build-python.sh`: build the portable Python runtime used by packaged Electron
- `start_chat.sh`: recommended local multi-peer launcher on macOS/Linux
- `start_chat.ps1`: Windows-oriented experimental starter

## How to run / verify

From `paramind/apps/desktop/`:

```bash
bash scripts/start_chat.sh
npm run build:python
```

On Windows PowerShell:

```powershell
./scripts/start_chat.ps1
```

## Dependencies / related modules

- Desktop app overview: [`../README.md`](../README.md)
- Backend details: [`../python/README.md`](../python/README.md)

## Current status / limitations

These scripts reflect the current prototype workflow and platform assumptions. Windows support is still experimental, and packaging support is strongest on the desktop platforms already covered in the app README.
