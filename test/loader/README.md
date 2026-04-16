# Loader Test Group

## What this module is

`test/loader/` is a legacy or experimental test area for older loader and transport-related scripts. It contains helper-style files that were useful during earlier development of model loading and peer communication experiments.

## Directory structure

```text
test/loader/
├── comm_utils.py
├── download.py
├── model_info.py
├── node1.py
├── node2.py
└── p2p_core.py
```

## Key responsibilities / boundaries

This directory is not a modern pytest-style test suite in the same sense as the other `test/*` groups. It is better understood as an experimental sandbox for older loader/P2P workflows.

## How to run / verify

There is no single canonical pytest command for this directory. Read the individual scripts before using them, and prefer the maintained test groups under [`../inference/README.md`](../inference/README.md) and [`../p2p/README.md`](../p2p/README.md) for current verification.

## Dependencies / related modules

- Current execution coverage: [`../inference/README.md`](../inference/README.md)
- Current transport coverage: [`../p2p/README.md`](../p2p/README.md)

## Current status / limitations

Treat this directory as historical/experimental support code rather than as the authoritative loader test suite. It is still worth documenting so readers know it exists, but it should not be the first verification target for current development.
