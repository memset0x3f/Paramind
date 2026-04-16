# Common Module

## What this module is

`common/` contains lightweight shared building blocks that are reused across transport, scheduling, and runtime execution. It is the place for small foundational types and helpers that are not specific to a single subsystem.

## Directory structure

```text
common/
├── Concepts.py     # shared runtime/network concepts and exceptions
├── Config.py       # TOML-backed config wrapper
├── Constants.py    # small global constants used by transport/runtime code
└── Utils.py        # socket, serialization, port, and registry helpers
```

## Key responsibilities / boundaries

This module owns:
- shared exception and peer/address concepts,
- small configuration helpers,
- constants that need to be imported across modules,
- transport-adjacent utilities such as UDP tensor chunk helpers.

This module does **not** own:
- planning policy (see [`../scheduler/README.md`](../scheduler/README.md)),
- shard execution semantics (see [`../inference/README.md`](../inference/README.md)),
- desktop app state or UI concerns.

When a helper becomes deeply specific to one subsystem, it should move out of `common/` and into that subsystem.

## Key entrypoints or important files

- `Concepts.py`: `RuntimeException`, `Site`, `PeerInfo`
- `Config.py`: `Config` for TOML load/save
- `Constants.py`: UDP/chunk timeout constants shared by transport code
- `Utils.py`: signal-server lookup, socket/port helpers, tensor packet encode/decode, and `FunctionRegistry`

## How to run / verify

There is no standalone executable entrypoint for `common/`. The most direct verification path is the shared test group in [`../test/common/README.md`](../test/common/README.md).

## Dependencies / related modules

- Used by [`../p2p/README.md`](../p2p/README.md)
- Used by [`../scheduler/README.md`](../scheduler/README.md)
- Used by [`../inference/README.md`](../inference/README.md)

## Current status / limitations

`common/` is intentionally small. It contains a mix of older utilities and current shared types, so its long-term health depends on keeping only genuinely cross-module code here. It should not become a catch-all utilities bucket.
